"""The examples run, and the numbers quoted in the README are the numbers toolism produces."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import toolism
from conftest import requires_tiktoken

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_files_slim_with_savings(path: Path) -> None:
    toolset = toolism.load(path)
    result = toolism.slim(toolset, tokenizer="heuristic", verify=True)
    assert result.report.saved > 0
    assert not result.report.rollbacks


@pytest.mark.parametrize("script", ["agent_startup.py", "openai_startup.py"])
def test_startup_examples_run_offline(script: str) -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "examples" / script)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    match = re.fullmatch(r"tool definitions: (\d+) -> (\d+) tokens per request.*\n", out)
    assert match is not None, out
    assert int(match.group(2)) < int(match.group(1))


@requires_tiktoken
def test_readme_quickstart_numbers_are_real() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    toolset = toolism.load(ROOT / "examples" / "mcp_tools.json")
    report = toolism.measure(toolset, tokenizer="tiktoken:o200k_base")
    slim = toolism.slim(toolset, tokenizer="tiktoken:o200k_base").report
    assert f"{report.total} tokens across 3 tools (average {report.total // 3})" in readme
    assert f"Slimmed 3 tools: {slim.before} → {slim.after} tokens" in readme
    assert f"baseline {slim.after}, +{slim.saved}," in readme
    assert f"({slim.saved} tokens saved\nper request)" in readme
    assert f"saves {slim.saved} × $3 = ${slim.saved * 3:,} a month" in readme
    for tool in report.tools:
        assert re.search(rf"│ {tool.name}\s+│\s+{tool.tokens} │", readme), tool.name


def test_readme_benchmark_numbers_match_results() -> None:
    import json

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    results = json.loads((ROOT / "bench" / "results.json").read_text(encoding="utf-8"))
    total = results["totals"]["safe"]["saved_pct"]
    median = results["median_saved_pct"]["safe"]
    assert f"**{total:.1f}% in total, with a median of {median:.1f}% per tool set**" in readme
    assert f"On {len(results['snapshots'])} tool sets ({results['total_tools']} tools" in readme


def test_measure_savings_example_runs_offline() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "measure_savings.py")],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    match = re.search(
        r"tokens before: (\d+)\ntokens after:\s+(\d+)\nsaved:\s+(\d+) \((\d+\.\d+)%\)",
        out,
    )
    assert match is not None, out
    before = int(match.group(1))
    after = int(match.group(2))
    saved = int(match.group(3))
    pct = float(match.group(4))
    assert after < before
    assert saved == before - after
    assert 0 < pct < 100
