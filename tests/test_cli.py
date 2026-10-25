from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from toolism import cli
from toolism.__about__ import __version__

FIXTURES = Path(__file__).parent / "fixtures" / "formats"
MCP_TOOLS = FIXTURES / "mcp_tools.json"
DEMO = Path(__file__).parent / "servers" / "demo_server.py"

BLOATED = {
    "tools": [
        {
            "name": "create_issue",
            "description": "Create an issue.\n\n\n    Use it when asked.   ",
            "inputSchema": {
                "type": "object",
                "title": "create_issueArguments",
                "properties": {
                    "title": {"type": "string", "title": "Title", "description": "The title."},
                    "body": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "title": "Body",
                        "description": "Markdown body of the issue. " * 8,
                        "examples": ["Steps to reproduce: ..."],
                    },
                },
                "required": ["title"],
            },
        },
        {"name": "ping", "description": "Ping.", "inputSchema": {"type": "object"}},
    ]
}


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("TOOLISM_TOKENIZER", "heuristic")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("TOOLISM_DEBUG", raising=False)
    return CliRunner()


@pytest.fixture
def bloated(tmp_path: Path) -> Path:
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(BLOATED), encoding="utf-8")
    return path


def invoke(runner: CliRunner, *args: Any, **kwargs: Any) -> Any:
    return runner.invoke(cli.app, [str(a) for a in args], **kwargs)


# ------------------------------------------------------------------- general


def test_version(runner: CliRunner) -> None:
    result = invoke(runner, "--version")
    assert result.exit_code == 0
    assert result.stdout == f"toolism {__version__}\n"


def test_help_lists_commands(runner: CliRunner) -> None:
    result = invoke(runner, "--help")
    assert result.exit_code == 0
    for command in ("measure", "slim", "diff", "check", "rules"):
        assert command in result.stdout


def test_python_dash_m() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "toolism", "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.stdout.strip() == f"toolism {__version__}"


# ------------------------------------------------------------------- measure


def test_measure_table(runner: CliRunner) -> None:
    result = invoke(runner, "measure", MCP_TOOLS)
    assert result.exit_code == 0, result.output
    assert "create_issue" in result.stdout
    assert "heuristic (approximate)" in result.stdout
    assert "\x1b[" not in result.stdout  # not a TTY: no colour
    assert result.stderr == ""


def test_measure_json_and_tokenizer_flag(runner: CliRunner) -> None:
    result = invoke(runner, "measure", MCP_TOOLS, "-f", "json", "-t", "heuristic")
    data = json.loads(result.stdout)
    assert data["kind"] == "measure"
    assert data["tokenizer"] == "heuristic"
    assert [t["name"] for t in data["tools"]] == ["create_issue", "ping"]


def test_measure_stdin_and_output_file(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "reports" / "tokens.md"
    result = invoke(
        runner,
        "measure",
        "-",
        "-f",
        "markdown",
        "-o",
        out,
        input=MCP_TOOLS.read_text(encoding="utf-8"),
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert out.read_text(encoding="utf-8").startswith("### toolism — tool definition tokens")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "error: missing SOURCE"),
        ((MCP_TOOLS, "--mcp", "server"), "error: give either SOURCE or --mcp, not both"),
        (("missing.json",), "error: file not found: missing.json"),
        ((MCP_TOOLS, "-t", "bogus"), "error: unknown tokenizer 'bogus'"),
    ],
)
def test_measure_input_errors_exit_2(
    runner: CliRunner, args: tuple[Any, ...], message: str
) -> None:
    result = invoke(runner, "measure", *args)
    assert result.exit_code == 2
    assert result.stderr.startswith(message)
    assert "Traceback" not in result.output


def test_invalid_json_reports_position(runner: CliRunner) -> None:
    result = invoke(runner, "measure", "-", input='{"tools": [}')
    assert result.exit_code == 2
    assert "invalid JSON at line 1, column 12" in result.stderr


def test_missing_input_file_shows_hint(runner: CliRunner) -> None:
    result = invoke(runner, "measure", "missing.json")
    assert result.exit_code == 2
    assert result.stderr == (
        "error: file not found: missing.json\n"
        "hint: check the path, or pass '-' to read from stdin (or --mcp to read from a server)\n"
    )


def test_unavailable_tokenizer_exits_3(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism.errors import TokenizerUnavailable
    from toolism.tokenizers import TiktokenTokenizer

    def unavailable_load(encoding: str) -> Any:
        raise TokenizerUnavailable("cannot load o200k_base", hint="set TIKTOKEN_CACHE_DIR")

    # Stub the vocabulary load so the test does not touch the network (or a warm cache).
    monkeypatch.setattr(TiktokenTokenizer, "_load", staticmethod(unavailable_load))
    result = invoke(runner, "measure", MCP_TOOLS, "-t", "tiktoken:o200k_base")
    assert result.exit_code == 3
    assert result.stderr == "error: cannot load o200k_base\nhint: set TIKTOKEN_CACHE_DIR\n"


def test_debug_reraises_toolism_errors(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism.errors import FormatError

    monkeypatch.setenv("TOOLISM_DEBUG", "1")
    result = invoke(runner, "measure", "missing.json")
    assert isinstance(result.exception, FormatError)


def test_debug_reraises_tokenizer_unavailable(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from toolism.errors import TokenizerUnavailable
    from toolism.tokenizers import TiktokenTokenizer

    def unavailable_load(encoding: str) -> Any:
        raise TokenizerUnavailable("cannot load o200k_base", hint="set TIKTOKEN_CACHE_DIR")

    # Same stub as test_unavailable_tokenizer_exits_3: no network, no cache.
    monkeypatch.setattr(TiktokenTokenizer, "_load", staticmethod(unavailable_load))
    monkeypatch.setenv("TOOLISM_DEBUG", "1")
    result = invoke(runner, "measure", MCP_TOOLS, "-t", "tiktoken:o200k_base")
    assert isinstance(result.exception, TokenizerUnavailable)


def test_internal_errors_are_one_line_unless_debug(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("kaput")

    monkeypatch.setattr("toolism.measurement.measure", boom)
    result = invoke(runner, "measure", MCP_TOOLS)
    assert result.exit_code == 2
    assert result.stderr.startswith(
        "error: internal error: RuntimeError: kaput\nhint: this is a bug"
    )
    monkeypatch.setenv("TOOLISM_DEBUG", "1")
    result = invoke(runner, "measure", MCP_TOOLS)
    assert isinstance(result.exception, RuntimeError)


# ---------------------------------------------------------------------- slim


def test_slim_separates_json_and_report(runner: CliRunner, bloated: Path) -> None:
    result = invoke(runner, "slim", bloated)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)  # stdout is clean JSON
    schema = data["tools"][0]["inputSchema"]
    assert "title" not in schema
    assert schema["properties"]["body"]["type"] == ["string", "null"]
    assert "Slimmed 2 tools" in result.stderr
    assert "SL001" in result.stderr


def test_slim_output_file_compact_and_quiet(
    runner: CliRunner, bloated: Path, tmp_path: Path
) -> None:
    out = tmp_path / "slim.json"
    result = invoke(runner, "slim", bloated, "-o", out, "--compact", "-q")
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    text = out.read_text(encoding="utf-8")
    assert text.count("\n") == 1
    assert json.loads(text)["tools"][1] == {
        "name": "ping",
        "description": "Ping.",
        "inputSchema": {"type": "object"},
    }


def test_slim_json_report_to_file(runner: CliRunner, bloated: Path, tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    result = invoke(runner, "slim", bloated, "-r", "json", "--report-file", report)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["kind"] == "slim"
    assert data["saved_tokens"] > 0


def test_slim_level_and_options(runner: CliRunner, bloated: Path) -> None:
    safe = json.loads(invoke(runner, "slim", bloated, "-q").stdout)
    assert "examples" in safe["tools"][0]["inputSchema"]["properties"]["body"]
    lossy = invoke(
        runner, "slim", bloated, "-q", "--level", "annotations", "--option", "SL101.max_chars=40"
    )
    assert lossy.exit_code == 0, lossy.output
    body = json.loads(lossy.stdout)["tools"][0]["inputSchema"]["properties"]["body"]
    assert "examples" not in body
    assert len(body["description"]) <= 40


def test_slim_enable_disable_with_commas(runner: CliRunner, bloated: Path) -> None:
    result = invoke(runner, "slim", bloated, "-q", "-e", "stable-order", "-d", "SL001,SL002")
    assert result.exit_code == 0, result.output
    tools = json.loads(result.stdout)["tools"]
    assert [t["name"] for t in tools] == ["create_issue", "ping"]
    schema = tools[0]["inputSchema"]
    assert schema["title"] == "create_issueArguments"  # SL001 disabled
    assert "anyOf" in schema["properties"]["body"]  # SL002 disabled


@pytest.mark.parametrize(
    ("args", "message", "hint"),
    [
        (("--option", "SL101max=3"), "error: invalid option 'SL101max=3'", "hint: use RULE.option"),
        (("--option", "SL101.max_chars=40"), "not enabled", "hint: add --enable SL101"),
        (
            ("-l", "annotations", "--option", "SL101.max_chars=lots"),
            "expects int",
            None,
        ),
        (("-e", "SL999"), "error: unknown rule 'SL999'", "hint: run 'toolism rules'"),
        (("--fail-on-rollback",), "error: --fail-on-rollback needs --verify", None),
    ],
)
def test_slim_option_errors(
    runner: CliRunner, bloated: Path, args: tuple[str, ...], message: str, hint: str | None
) -> None:
    result = invoke(runner, "slim", bloated, *args)
    assert result.exit_code == 2
    assert message in result.stderr
    if hint:
        assert hint in result.stderr
    assert result.stdout == ""


def test_slim_verify(runner: CliRunner, bloated: Path) -> None:
    pytest.importorskip("hypothesis_jsonschema")
    result = invoke(runner, "slim", bloated, "--verify", "--fail-on-rollback")
    assert result.exit_code == 0, result.output
    assert "Verification: 1 equivalent" in result.stderr


def test_slim_fail_on_rollback_exits_1(
    runner: CliRunner, bloated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from toolism import pipeline
    from toolism.pipeline import Rollback

    real = pipeline.slim

    def with_rollback(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **{**kwargs, "verify": False})
        report = type(result.report)(
            **{**result.report.__dict__, "rollbacks": (Rollback("ping", ("SL008",), "x", 1),)}
        )
        return type(result)(result.toolset, report)

    monkeypatch.setattr(pipeline, "slim", with_rollback)
    monkeypatch.setattr("toolism.pipeline.verify_available", lambda: True)
    result = invoke(runner, "slim", bloated, "--verify", "--fail-on-rollback", "-q")
    assert result.exit_code == 1
    assert json.loads(result.stdout)  # the slimmed JSON is still written
    assert "error: verification rolled back changes to 1 tool(s): ping" in result.stderr


# --------------------------------------------------------------------- check


def grow(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tools"][1]["description"] = "Ping the server. " * 20
    path.write_text(json.dumps(data), encoding="utf-8")


def test_check_baseline_workflow(runner: CliRunner, bloated: Path, tmp_path: Path) -> None:
    baseline = tmp_path / ".toolism" / "baseline.json"
    created = invoke(runner, "check", bloated, "-b", baseline, "--update-baseline")
    assert created.exit_code == 0, created.output
    assert created.stdout.startswith(f"Wrote {baseline}:")
    assert json.loads(baseline.read_text(encoding="utf-8"))["tokenizer"] == "heuristic"

    same = invoke(runner, "check", bloated, "-b", baseline)
    assert same.exit_code == 0, same.output
    assert "toolism check PASSED" in same.stdout

    grow(bloated)
    grown = invoke(runner, "check", bloated, "-b", baseline)
    assert grown.exit_code == 1
    assert "toolism check FAILED" in grown.stdout
    assert "over the allowed 0 tokens" in grown.stdout  # default: no growth
    assert grown.stderr == ""

    allowed = invoke(runner, "check", bloated, "-b", baseline, "--max-increase", "500%")
    assert allowed.exit_code == 0, allowed.output
    both = invoke(
        runner, "check", bloated, "-b", baseline, "--max-increase", "1%", "--max-increase", "5000"
    )
    assert both.exit_code == 0, both.output


def test_check_writes_markdown_summaries(
    runner: CliRunner, bloated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step_summary = tmp_path / "step_summary.md"
    step_summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    summary = tmp_path / "summary.md"
    result = invoke(
        runner, "check", bloated, "--max-tool", "10", "--summary-md", summary, "-f", "json"
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False
    assert summary.read_text(encoding="utf-8").startswith(
        "### ❌ toolism — tool token budget failed"
    )
    appended = step_summary.read_text(encoding="utf-8")
    assert appended.startswith("earlier step\n### ❌ toolism")
    assert "over the per-tool cap of 10" in appended


def test_check_caps_without_baseline(runner: CliRunner, bloated: Path) -> None:
    assert invoke(runner, "check", bloated, "--max-total", "100000").exit_code == 0
    result = invoke(runner, "check", bloated, "--max-total", "1", "-f", "markdown")
    assert result.exit_code == 1
    assert "over the cap of 1" in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "nothing to check"),
        (("--update-baseline",), "--update-baseline needs --baseline FILE"),
        (("--max-increase", "10%", "--max-total", "5"), "--max-increase needs --baseline"),
        (("-b", "b.json", "--max-increase", "lots"), "invalid increase 'lots'"),
        (("-b", "b.json", "--max-increase", "1%", "--max-increase", "2%"), "at most one"),
        (
            (
                "-b",
                "does-not-exist.json",
            ),
            "baseline not found",
        ),
    ],
)
def test_check_usage_errors(
    runner: CliRunner, bloated: Path, args: tuple[str, ...], message: str
) -> None:
    result = invoke(runner, "check", bloated, *args)
    assert result.exit_code == 2
    assert result.stderr.startswith("error: ")
    assert message in result.stderr


def test_check_tokenizer_mismatch(runner: CliRunner, bloated: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    invoke(runner, "check", bloated, "-b", baseline, "--update-baseline")
    data = json.loads(baseline.read_text(encoding="utf-8"))
    baseline.write_text(json.dumps({**data, "tokenizer": "tiktoken:o200k_base"}), encoding="utf-8")
    result = invoke(runner, "check", bloated, "-b", baseline)
    assert result.exit_code == 2
    assert "baseline was measured with tiktoken:o200k_base" in result.stderr
    assert "hint: " in result.stderr
    allowed = invoke(runner, "check", bloated, "-b", baseline, "--allow-tokenizer-change")
    assert allowed.exit_code == 0
    assert "growth budgets were not enforced" in allowed.stdout


# --------------------------------------------------------------------- rules


def test_rules_formats(runner: CliRunner, tmp_path: Path) -> None:
    table = invoke(runner, "rules")
    assert table.exit_code == 0
    assert "SL001" in table.stdout
    assert "redundant-title" in table.stdout
    data = json.loads(invoke(runner, "rules", "-f", "json").stdout)
    assert data["kind"] == "rules"
    by_id = {r["id"]: r for r in data["rules"]}
    assert by_id["SL001"]["enabled_by"] == "default"
    assert by_id["SL007"]["enabled_by"] == "--enable SL007"
    assert by_id["SL101"]["enabled_by"] == "--level annotations"
    assert by_id["SL101"]["options"][0]["name"] == "max_chars"
    out = tmp_path / "rules.md"
    assert invoke(runner, "rules", "-f", "markdown", "-o", out).exit_code == 0
    assert out.read_text(encoding="utf-8").startswith("# Rules\n")


# ---------------------------------------------------------------- MCP and IO


def test_mcp_without_extra(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism.errors import MissingExtraError

    def missing() -> Any:
        raise MissingExtraError("Loading tools from an MCP server", "mcp")

    monkeypatch.setattr("toolism.loaders.mcp._import_mcp", missing)
    result = invoke(runner, "measure", "--mcp", "npx -y some-server")
    assert result.exit_code == 2
    assert "hint: pip install 'toolism[mcp]'" in result.stderr


@pytest.mark.mcp
def test_measure_live_mcp_server(runner: CliRunner) -> None:
    pytest.importorskip("mcp")
    command = f'"{sys.executable}" "{DEMO}"'
    result = invoke(runner, "measure", "--mcp", command, "-f", "json")
    assert result.exit_code == 0, result.output
    assert [t["name"] for t in json.loads(result.stdout)["tools"]] == [
        "create_contact",
        "search_contacts",
    ]


def test_unwritable_output(runner: CliRunner, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    result = invoke(runner, "measure", MCP_TOOLS, "-o", blocker / "x.txt")
    assert result.exit_code == 2
    assert result.stderr.startswith("error: cannot write")


class FakeTTY:
    def isatty(self) -> bool:
        return True


def test_style_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli._style(object()) == (cli.DEFAULT_WIDTH, False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    width, color = cli._style(FakeTTY())
    assert width >= 60
    assert color
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._style(FakeTTY())[1] is False


def test_main_switches_non_utf8_streams_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    cli._utf8_streams()
    assert out.encoding == "utf-8"
    out.write("— ✅ ünïcode")  # would raise UnicodeEncodeError under cp1252
    out.flush()


# ---------------------------------------------------------------------- diff


def test_diff_changed_tool_and_unchanged_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "tools.json"
    original = json.dumps(
        {
            "tools": [
                {"name": "search", "inputSchema": {"title": "searchArguments", "type": "object"}},
                {"name": "ping", "inputSchema": {"type": "object"}},
            ]
        }
    )
    source.write_text(original, encoding="utf-8")
    # A preview must not consult the user's possibly remote reporting tokenizer.
    monkeypatch.setenv("TOOLISM_TOKENIZER", "not-a-tokenizer")
    result = invoke(runner, "diff", source)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout == (
        '--- "search" (before)\n'
        '+++ "search" (after)\n'
        "@@ -1,7 +1,6 @@\n"
        " {\n"
        '   "name": "search",\n'
        '   "parameters": {\n'
        '-    "title": "searchArguments",\n'
        '     "type": "object"\n'
        "   }\n"
        " }\n"
    )
    assert source.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"tools": []},
        [{"name": "ping", "inputSchema": {"type": "object"}}],
        [{"type": "web_search_20250305", "name": "web_search"}],
    ],
)
def test_diff_omits_unchanged_tools(runner: CliRunner, document: Any) -> None:
    result = invoke(runner, "diff", "-", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize(
    ("args", "changes"),
    [
        ((), False),
        (("--level", "annotations"), True),
        (("--enable", "SL102"), True),
        (("--level", "annotations", "-e", "SL007,SL102", "-d", "drop-examples"), False),
    ],
)
def test_diff_uses_slim_level_and_rule_selection(
    runner: CliRunner, args: tuple[str, ...], changes: bool
) -> None:
    document = [
        {
            "name": "search",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string", "examples": ["sample"]}},
            },
        }
    ]
    result = invoke(runner, "diff", "-", *args, input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    if changes:
        assert '-        "examples": [\n' in result.stdout
        assert '-          "sample"\n' in result.stdout
    else:
        assert result.stdout == ""


def test_diff_applies_repeated_rule_options_to_tool_description(runner: CliRunner) -> None:
    document = [
        {
            "name": "search",
            "description": "A long description with additional details for readers.",
            "inputSchema": {"type": "object"},
        }
    ]
    result = invoke(
        runner,
        "diff",
        "-",
        "-e",
        "SL101",
        "--option",
        "SL101.max_chars=20",
        "--option",
        "SL101.include_tool=true",
        input=json.dumps(document),
    )
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert (
        '-  "description": "A long description with additional details for readers.",'
        in result.stdout
    )
    assert '+  "description": "A long description' in result.stdout


def test_diff_shows_description_only_cleanup(runner: CliRunner) -> None:
    document = [{"name": "search", "description": "Search.   ", "inputSchema": {"type": "object"}}]
    result = invoke(runner, "diff", "-", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert '-  "description": "Search.   ",' in result.stdout
    assert '+  "description": "Search.",' in result.stdout


def test_diff_does_not_confuse_tool_reordering_with_schema_changes(runner: CliRunner) -> None:
    document = [{"name": name, "inputSchema": {"type": "object"}} for name in ("z", "a")]
    result = invoke(runner, "diff", "-", "--enable", "SL007", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stdout == result.stderr == ""


def test_diff_retains_duplicate_occurrences_when_tools_are_reordered(runner: CliRunner) -> None:
    document = [
        {"name": "z", "inputSchema": {"type": "object"}},
        {
            "name": "same",
            "inputSchema": {
                "type": "object",
                "properties": {"first": {"type": "string", "default": None}},
            },
        },
        {
            "name": "same",
            "inputSchema": {
                "type": "object",
                "properties": {"second": {"type": "string", "default": None}},
            },
        },
        {"name": "a", "inputSchema": {"type": "object"}},
    ]
    result = invoke(runner, "diff", "-", "-e", "SL007", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stdout.count('--- "same"') == 2
    assert '--- "same" [1] (before)' in result.stdout
    assert '--- "same" [2] (before)' in result.stdout
    assert result.stdout.index('--- "same" [1]') < result.stdout.index('--- "same" [2]')
    assert result.stdout.count('-        "default": null,') == 2
    assert '--- "z"' not in result.stdout
    assert '--- "a"' not in result.stdout
    assert "Warning: duplicate tool name 'same' (2 times)" in result.stderr


def test_diff_writes_output_file(runner: CliRunner, bloated: Path, tmp_path: Path) -> None:
    target = tmp_path / "reports" / "tools.diff"
    result = invoke(runner, "diff", bloated, "--output", target)
    assert result.exit_code == 0, result.output
    assert result.stdout == result.stderr == ""
    assert target.read_text(encoding="utf-8").startswith('--- "create_issue" (before)\n')


def test_diff_replaces_stale_output_with_empty_result(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "tools.diff"
    target.write_text("old diff", encoding="utf-8")
    result = invoke(runner, "diff", "-", "-o", target, input="[]")
    assert result.exit_code == 0, result.output
    assert result.stdout == result.stderr == ""
    assert target.read_bytes() == b""


def test_diff_headers_escape_controls_and_preserve_unicode(runner: CliRunner) -> None:
    document = [
        {
            "name": "café\n\x1b[31m",
            "inputSchema": {
                "type": "object",
                "properties": {"x": {"type": "string", "default": None}},
            },
        }
    ]
    result = invoke(runner, "diff", "-", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith('--- "café\\n\\u001b[31m" (before)\n')
    assert "\x1b" not in result.stdout


@pytest.mark.parametrize(
    ("source", "args", "message"),
    [
        ("not json", (), "invalid JSON"),
        ("[]", ("-e", "NOT_A_RULE"), "unknown rule"),
        ("[]", ("-e", "SL101", "--option", "SL101.max_chars=oops"), "max_chars"),
    ],
)
def test_diff_reports_real_input_and_selection_errors(
    runner: CliRunner, source: str, args: tuple[str, ...], message: str
) -> None:
    result = invoke(runner, "diff", "-", *args, input=source)
    assert result.exit_code == 2
    assert message in result.stderr
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("separator", "escaped"),
    [
        ("\u0085", "\\u0085"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ],
)
def test_diff_keeps_lf_hunks_with_unicode_line_separators(
    runner: CliRunner, separator: str, escaped: str
) -> None:
    document = [
        {
            "name": f"line{separator}name",
            "description": f"one{separator}two  ",
            "inputSchema": {"type": "object"},
        }
    ]
    result = invoke(runner, "diff", "-", input=json.dumps(document))
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f'--- "line{escaped}name" (before)\n')
    assert "@@ -1,5 +1,5 @@\n" in result.stdout
    assert f'   "name": "line{separator}name",\n' in result.stdout


def test_diff_requires_source(runner: CliRunner) -> None:
    result = invoke(runner, "diff")
    assert result.exit_code == 2
    assert "missing argument" in result.output.lower()
    assert "SOURCE" in result.output


def test_diff_reports_output_write_errors(runner: CliRunner, bloated: Path, tmp_path: Path) -> None:
    result = invoke(runner, "diff", bloated, "-o", tmp_path)
    assert result.exit_code == 2
    assert "cannot write" in result.stderr


def test_architecture_doc_lists_every_cli_option() -> None:
    """docs/architecture/ARCHITECTURE.md section 10 is the CLI reference: keep it complete."""
    import typer.main

    doc = (Path(__file__).resolve().parents[1] / "docs/architecture/ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    group = typer.main.get_command(cli.app)
    for name, command in group.commands.items():  # type: ignore[attr-defined]
        for param in command.params:
            for opt in param.opts:
                if opt.startswith("--") and opt != "--help":
                    assert opt in doc, f"toolism {name} {opt} is missing from ARCHITECTURE.md"


@pytest.mark.parametrize("selector", ["SL004", "inline-defs"])
@pytest.mark.parametrize("fmt", ["table", "json", "markdown"])
@pytest.mark.parametrize("to_file", [False, True])
def test_rules_single_detail(
    runner: CliRunner, tmp_path: Path, selector: str, fmt: str, to_file: bool
) -> None:
    from toolism.rules import get_rule

    args = ["rules", selector, "-f", fmt]
    output = tmp_path / "rule.txt"
    if to_file:
        args += ["-o", str(output)]
    result = invoke(runner, *args)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    text = output.read_text(encoding="utf-8") if to_file else result.stdout
    if to_file:
        assert result.stdout == ""
    if fmt == "json":
        data = json.loads(text)
        assert data["kind"] == "rules"
        assert [rule["id"] for rule in data["rules"]] == ["SL004"]
    else:
        description = inspect.cleandoc(get_rule("SL004").__doc__ or "").replace("``", "`")
        assert " ".join(description.split()) in " ".join(text.split())
        assert "SL004" in text
        assert "inline-defs" in text
        assert "SL001" not in text
        assert "safe" in text
        assert "default" in text
        assert "Validation-equivalent" in text


def test_rules_single_detail_options(runner: CliRunner) -> None:
    result = invoke(runner, "rules", "SL101")
    assert result.exit_code == 0, result.output
    assert "max_chars" in result.stdout
    assert "300" in result.stdout
    assert "int" in result.stdout
    assert "--level annotations" in result.stdout


@pytest.mark.parametrize("selector", ["SL999", "not-a-rule"])
def test_rules_unknown_uses_existing_error(runner: CliRunner, selector: str) -> None:
    result = invoke(runner, "rules", selector)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        f"error: unknown rule {selector!r}\nhint: run 'toolism rules' to list rules\n"
    )
