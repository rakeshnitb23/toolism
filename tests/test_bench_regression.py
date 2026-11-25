"""The benchmark gate must detect per-cell regressions without rewriting its baseline."""

from __future__ import annotations

import copy
import importlib.util
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bench" / "run.py"
SPEC = importlib.util.spec_from_file_location("benchmark_run", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def report() -> dict[str, Any]:
    return {
        "toolism_report": 1,
        "kind": "benchmark",
        "tokenizers": list(bench.OFFLINE_TOKENIZERS),
        "snapshots": [
            {
                "name": name,
                "tools": 2,
                "levels": {
                    level: {
                        tokenizer: {"before": 1000, "after": 900, "saved": 100, "saved_pct": 10.0}
                        for tokenizer in bench.OFFLINE_TOKENIZERS
                    }
                    for level in bench.LEVELS
                },
            }
            for name in ("alpha", "beta")
        ],
    }


def cell(
    data: dict[str, Any], index: int = 0, level: str = "safe", tokenizer: str = "heuristic"
) -> dict[str, Any]:
    return data["snapshots"][index]["levels"][level][tokenizer]


def set_saved(stats: dict[str, Any], saved: int) -> None:
    stats["saved"] = saved
    stats["after"] = stats["before"] - saved


def test_equal_and_improved_results() -> None:
    baseline = report()
    current = copy.deepcopy(baseline)
    current["date"] = "2099-01-01"
    assert bench.compare_results(baseline, current, Fraction(0)) == []
    set_saved(cell(current), 101)
    assert bench.compare_results(baseline, current, Fraction(0)) == []


@pytest.mark.parametrize("level", bench.LEVELS)
@pytest.mark.parametrize("tokenizer", bench.OFFLINE_TOKENIZERS)
def test_loss_identifies_cell_even_when_total_improves(level: str, tokenizer: str) -> None:
    baseline, current = report(), report()
    set_saved(cell(current, 0, level, tokenizer), 99)
    set_saved(cell(current, 1, level, tokenizer), 150)
    errors = bench.compare_results(baseline, current, Fraction(0))
    assert len(errors) == 1
    assert f"alpha / {level} / {tokenizer}" in errors[0]
    assert "100 -> 99 tokens" in errors[0]


def test_explicit_margin_is_exact_not_rounded() -> None:
    baseline, current = report(), report()
    set_saved(cell(current), 99)
    assert bench.compare_results(baseline, current, Fraction("0.1")) == []
    assert bench.compare_results(baseline, current, Fraction("0.09999"))


def test_ignores_remote_tokenizer_fields() -> None:
    baseline, current = report(), report()
    baseline["tokenizers"].append("anthropic:example")
    baseline["snapshots"][0]["levels"]["safe"]["anthropic:example"] = {}
    assert bench.compare_results(baseline, current, Fraction(0)) == []


@pytest.mark.parametrize("case", ["missing", "new", "duplicate", "empty", "count", "before"])
def test_incompatible_corpus_fails_closed(case: str) -> None:
    baseline, current = report(), report()
    if case == "missing":
        current["snapshots"].pop()
    elif case == "new":
        row = copy.deepcopy(current["snapshots"][0])
        row["name"] = "new"
        current["snapshots"].append(row)
    elif case == "duplicate":
        current["snapshots"].append(current["snapshots"][0])
    elif case == "empty":
        current["snapshots"].clear()
    elif case == "count":
        current["snapshots"][0]["tools"] += 1
    else:
        cell(current)["before"] += 1
        cell(current)["after"] += 1
    with pytest.raises(ValueError, match=r"tool set|tool count|unslimmed count"):
        bench.compare_results(baseline, current, Fraction(0))


@pytest.mark.parametrize("case", ["tokenizer", "inconsistent", "boolean", "negative", "version"])
def test_invalid_baseline_rejected(case: str) -> None:
    baseline = report()
    if case == "tokenizer":
        baseline["tokenizers"].remove("heuristic")
    elif case == "inconsistent":
        cell(baseline)["saved"] = 200
    elif case == "boolean":
        cell(baseline)["before"] = True
    elif case == "negative":
        cell(baseline)["after"] = -1
    else:
        baseline["toolism_report"] = 2
    with pytest.raises(ValueError, match=r"report|token counts"):
        bench.compare_results(baseline, report(), Fraction(0))


def test_check_mode_preserves_reports_and_forces_local_tokenizers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "results.json"
    markdown = tmp_path / "RESULTS.md"
    original = json.dumps(report()).encode()
    baseline.write_bytes(original)
    markdown.write_bytes(b"unchanged")
    current = report()
    calls = []

    def tokenizers(*, offline: bool = False) -> list:
        calls.append(offline)
        return []

    monkeypatch.setattr(bench, "BENCH", tmp_path)
    monkeypatch.setattr(bench, "available_tokenizers", tokenizers)
    monkeypatch.setattr(bench, "collect_results", lambda _: current)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    assert bench.main(["--check"]) == 0
    set_saved(cell(current), 99)
    assert bench.main(["--check"]) == 1
    assert bench.main(["--check", "--max-loss-pp", "0.1"]) == 0
    assert calls == [True, True, True]
    assert baseline.read_bytes() == original
    assert markdown.read_bytes() == b"unchanged"


def test_missing_baseline_returns_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "BENCH", tmp_path)
    assert bench.main(["--check"]) == 1
    assert not (tmp_path / "results.json").exists()


@pytest.mark.parametrize("args", [["--check", "--max-loss-pp", "-1"], ["--max-loss-pp", "1"]])
def test_invalid_margin_argument(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        bench.main(args)
    assert exc.value.code == 2


def test_offline_tokenizers_never_request_remote_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeTokenizer:
        def count_tools(self, tools: Any) -> None:
            pass

    def get_tokenizer(spec: str) -> FakeTokenizer:
        calls.append(spec)
        return FakeTokenizer()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(bench, "get_tokenizer", get_tokenizer)
    assert len(bench.available_tokenizers(offline=True)) == 3
    assert calls == list(bench.OFFLINE_TOKENIZERS)
