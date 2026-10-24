from __future__ import annotations

import json
from typing import Any

import pytest

from toolism.formats import parse
from toolism.measurement import TokenReport, ToolCount, measure
from toolism.pipeline import Rollback, slim
from toolism.report import (
    REPORT_VERSION,
    render_slim_report,
    render_token_report,
    slim_report_to_dict,
    token_report_to_dict,
)
from toolism.tokenizers import HeuristicTokenizer
from toolism.verify import Verdict, VerificationResult

H = HeuristicTokenizer()

TOOLS = parse(
    {
        "tools": [
            {
                "name": "search",
                "description": "Search.",
                "inputSchema": {
                    "type": "object",
                    "title": "searchArguments",
                    "properties": {"q": {"type": "string", "title": "Q"}},
                },
            },
            {"name": "ping", "inputSchema": {"type": "object"}},
        ]
    }
)


def test_measure_counts_function_tools() -> None:
    ts = parse(
        [
            {"name": "a", "input_schema": {"type": "object"}},
            {"type": "web_search_20250305", "name": "web_search"},
        ]
    )
    report = measure(ts, H)
    assert report.tokenizer == "heuristic"
    assert report.approximate
    assert report.tool_count == 1
    assert report.total == H.count_tool(ts.tools[0])
    assert report.tools == (ToolCount("a", report.total),)
    assert report.uncounted == ("web_search",)
    assert report.average == report.total


def test_empty_measure() -> None:
    report = measure(parse([]), H)
    assert (report.total, report.tool_count, report.average) == (0, 0, 0.0)
    assert "0 tokens across 0 tools" in render_token_report(report)


def test_token_report_dict_and_json() -> None:
    report = measure(TOOLS, H)
    data = token_report_to_dict(report)
    assert data["toolism_report"] == REPORT_VERSION
    assert data["kind"] == "measure"
    assert data["total_tokens"] == report.total
    assert [t["name"] for t in data["tools"]] == ["search", "ping"]
    assert all(isinstance(t["tokens"], int) for t in data["tools"])
    assert round(sum(t["percent"] for t in data["tools"])) == 100
    assert json.loads(render_token_report(report, "json")) == data


def test_token_report_table_and_markdown() -> None:
    report = measure(TOOLS, H)
    table = render_token_report(report, "table", width=80)
    assert "search" in table
    assert "heuristic (approximate)" in table
    assert table.index("search") < table.index("ping")  # sorted by tokens
    md = render_token_report(report, "markdown")
    assert md.startswith("### toolism — tool definition tokens")
    assert "| `search` |" in md
    assert "| Tool | Tokens | % of total |" in md


@pytest.mark.parametrize("fmt", ["table", "markdown", "json"])
def test_measure_duplicate_name_warning_is_rendered_in_every_format(fmt: str) -> None:
    duplicate = {"name": "same", "input_schema": {"type": "object"}}
    toolset = parse([duplicate, duplicate])
    report = measure(toolset, H)

    warning = "duplicate tool name 'same' (2 times)"
    assert report.warnings == (warning,)
    rendered = render_token_report(report, fmt)  # type: ignore[arg-type]
    if fmt == "json":
        assert json.loads(rendered)["warnings"] == [warning]
    else:
        assert f"Warning: {warning}" in rendered


def test_token_report_truncates_long_tables() -> None:
    report = TokenReport(
        tokenizer="heuristic",
        approximate=True,
        total=3000,
        tool_count=30,
        tools=tuple(ToolCount(f"t{i:02}", 100) for i in range(30)),
        uncounted=("web_search",),
    )
    text = render_token_report(report, "table", width=200)
    assert "… 5 more tools (500 tokens)" in text
    assert "Not counted (provider built-ins): web_search" in text
    md = render_token_report(report, "markdown")
    assert md.count("| `t") == 25


def test_color_output_has_ansi_codes() -> None:
    report = measure(TOOLS, H)
    assert "\x1b[" in render_token_report(report, "table", color=True)
    assert "\x1b[" not in render_token_report(report, "table", color=False)


def test_slim_report_renderers() -> None:
    report = slim(TOOLS, tokenizer=H).report
    data = slim_report_to_dict(report)
    assert data["kind"] == "slim"
    assert data["saved_tokens"] == report.saved > 0
    assert data["before_tokens"] - data["after_tokens"] == data["saved_tokens"]
    assert json.loads(render_slim_report(report, "json")) == data
    table = render_slim_report(report, "table", width=120)
    assert "Slimmed 2 tools" in table
    assert "SL001" in table
    assert "redundant-title" in table
    md = render_slim_report(report, "markdown")
    assert "| `SL001` | redundant-title |" in md
    assert "| Tool | Before | After | Saved |" in md


def test_slim_report_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    report = slim(TOOLS, tokenizer=H).report
    noisy = type(report)(
        **{
            **report.__dict__,
            "warnings": ("something odd",),
            "rollbacks": (Rollback("search", ("SL906",), "original→slim", {"x": 1}),),
            "verification": (
                VerificationResult("search", Verdict.EQUIVALENT, method="annotations-only"),
                VerificationResult("ping", Verdict.EQUIVALENT, method="sampled"),
                VerificationResult("x", Verdict.SKIPPED, reason="recursive", method="sampled"),
            ),
            "uncounted": ("web_search",),
            "attribution_tokenizer": "heuristic",
            "tokenizer": "anthropic:claude",
            "approximate": False,
        }
    )
    text = render_slim_report(noisy, "markdown")
    assert "Warning: something odd" in text
    assert "Verification: 2 equivalent (1 proved, 1 sampled), 1 skipped, 0 counterexamples" in text
    assert "Not counted (provider built-ins): web_search" in text
    assert "Per-rule savings estimated with heuristic." in text
    assert "anthropic:claude —" in text or "anthropic:claude," in text
    data: dict[str, Any] = slim_report_to_dict(noisy)
    assert data["rollbacks"][0]["rules"] == ["SL906"]
    assert data["verification"][0]["method"] == "annotations-only"


def test_slim_report_with_no_changes() -> None:
    ts = parse([{"name": "a", "input_schema": {"type": "object"}}])
    report = slim(ts, tokenizer=H).report
    assert report.saved == 0
    text = render_slim_report(report, "table")
    assert "(0, −0.0%)" in text
    assert "Savings by rule" not in text
    md = render_slim_report(report, "markdown")
    assert "| Rule |" not in md


def test_docs_rules_md_is_up_to_date() -> None:
    """docs/rules.md is generated; regenerate with `toolism rules -f markdown -o docs/rules.md`."""
    from pathlib import Path

    from toolism.report import render_rules

    page = Path(__file__).resolve().parents[1] / "docs" / "rules.md"
    assert page.read_text(encoding="utf-8") == render_rules("markdown"), (
        "docs/rules.md is stale: run `uv run toolism rules -f markdown -o docs/rules.md`"
    )
