"""The ``toolism`` command line (design C11). A thin layer: no business logic lives here.

Exit codes: 0 OK, 1 check failed or rollback with ``--fail-on-rollback``, 2 usage/input error,
3 tokenizer unavailable.
"""

from __future__ import annotations

import contextlib
import functools
import os
import shutil
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, NoReturn, TypeVar

import typer

from toolism.__about__ import __version__
from toolism.errors import TokenizerUnavailable, ToolismError
from toolism.models import ToolSet

__all__ = ["app", "main"]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
EXIT_TOKENIZER = 3
ISSUES_URL = "https://github.com/rakeshnitb23/toolism/issues"
DEFAULT_WIDTH = 100


class OutputFormat(str, Enum):
    table = "table"
    json = "json"
    markdown = "markdown"


class Level(str, Enum):
    safe = "safe"
    annotations = "annotations"


app = typer.Typer(
    name="toolism",
    help=(
        "Measure and cut the tokens your LLM tool definitions cost on every request. "
        "Works with OpenAI, Anthropic and MCP tool JSON, or a live MCP server."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

# ------------------------------------------------------------------ plumbing


class UsageProblem(ToolismError):
    """Options that cannot be combined, or a missing input. Exit code 2."""


F = TypeVar("F", bound=Callable[..., Any])


def _print_error(message: str, hint: str | None = None) -> None:
    typer.echo(f"error: {message}", err=True)
    if hint:
        typer.echo(f"hint: {hint}", err=True)


def _handle_errors(func: F) -> F:
    """Map exceptions to one ``error:`` line (plus ``hint:``) and the documented exit codes."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except TokenizerUnavailable as exc:
            if os.environ.get("TOOLISM_DEBUG"):
                raise
            _print_error(exc.message, exc.hint)
            raise typer.Exit(EXIT_TOKENIZER) from None
        except ToolismError as exc:
            if os.environ.get("TOOLISM_DEBUG"):
                raise
            _print_error(exc.message, exc.hint)
            raise typer.Exit(EXIT_ERROR) from None
        except Exception as exc:
            # typer.Exit and friends (typer may vendor click, so match on the module)
            if type(exc).__module__.split(".")[0] in ("typer", "click"):
                raise
            if os.environ.get("TOOLISM_DEBUG"):
                raise
            _print_error(
                f"internal error: {type(exc).__name__}: {exc}",
                f"this is a bug; rerun with TOOLISM_DEBUG=1 and report it at {ISSUES_URL}",
            )
            raise typer.Exit(EXIT_ERROR) from None

    return wrapper  # type: ignore[return-value]


def _style(stream: Any) -> tuple[int, bool]:
    """Terminal width and colour for ``stream``: plain, 100 columns when it is not a TTY."""
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        tty = False
    if not tty:
        return DEFAULT_WIDTH, False
    width = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    return max(width, 60), "NO_COLOR" not in os.environ


def _write_file(path: Path, text: str, *, append: bool = False) -> None:
    try:
        if path.parent != Path():
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise ToolismError(f"cannot write {path}: {exc.strerror or exc}") from None


def _emit(text: str, output: Path | None, *, color: bool | None = None) -> None:
    if output is None:
        typer.echo(text, nl=False, color=color)
    else:
        _write_file(output, text)


def _load(source: str | None, mcp: str | None, timeout: float) -> ToolSet:
    if source is not None and mcp is not None:
        raise UsageProblem("give either SOURCE or --mcp, not both")
    if mcp is not None:
        from toolism.loaders.mcp import load_mcp_stdio

        return load_mcp_stdio(mcp, timeout=timeout)
    if source is None:
        raise UsageProblem("missing SOURCE: a JSON file, '-' for stdin, or --mcp 'COMMAND'")
    from toolism.io import load

    return load(source)


def _split(values: list[str] | None) -> list[str]:
    """``-e SL101 -e SL102,drop-examples`` → ``["SL101", "SL102", "drop-examples"]``."""
    return [part.strip() for value in values or [] for part in value.split(",") if part.strip()]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"toolism {__version__}")
        raise typer.Exit()


# Shared parameter declarations.
SourceArg = Annotated[
    str | None,
    typer.Argument(
        metavar="SOURCE",
        help="Tool definitions as JSON (OpenAI, Anthropic or MCP), or '-' for stdin.",
        show_default=False,
    ),
]
McpOpt = Annotated[
    str | None,
    typer.Option(
        "--mcp",
        metavar="COMMAND",
        help="Read tools from a stdio MCP server instead, e.g. --mcp 'npx -y @scope/server'.",
        show_default=False,
    ),
]
McpTimeoutOpt = Annotated[
    float,
    typer.Option("--mcp-timeout", metavar="SECONDS", help="Give up on the MCP server after this."),
]
TokenizerOpt = Annotated[
    str | None,
    typer.Option(
        "--tokenizer",
        "-t",
        metavar="NAME",
        help=(
            "heuristic, tiktoken:<encoding> or anthropic:<model>. "
            "Default: $TOOLISM_TOKENIZER, else tiktoken:o200k_base."
        ),
        show_default=False,
    ),
]
OutputOpt = Annotated[
    Path | None,
    typer.Option("--output", "-o", metavar="FILE", help="Write here instead of stdout."),
]


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Measure and cut the tokens your LLM tool definitions cost on every request."""


# ------------------------------------------------------------------ commands


@app.command()
@_handle_errors
def doctor() -> None:
    """Print local versions and optional dependencies for a bug report (no network)."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    typer.echo(f"toolism: {__version__}")
    typer.echo(f"Python: {platform.python_version()}")
    typer.echo(f"OS: {platform.system()} {platform.release()}")
    for package in (
        "typer",
        "rich",
        "tiktoken",
        "mcp",
        "anthropic",
        "hypothesis",
        "hypothesis-jsonschema",
        "jsonschema",
    ):
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "not installed"
        typer.echo(f"{package}: {installed}")
    # Report presence only, never environment values or arbitrary variable names.
    key_status = "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set"
    typer.echo(f"ANTHROPIC_API_KEY: {key_status}")


@app.command()
@_handle_errors
def measure(
    source: SourceArg = None,
    mcp: McpOpt = None,
    mcp_timeout: McpTimeoutOpt = 60.0,
    tokenizer: TokenizerOpt = None,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Report format.")
    ] = OutputFormat.table,
    output: OutputOpt = None,
) -> None:
    """Count the tokens each tool definition costs."""
    from toolism.measurement import measure as measure_toolset
    from toolism.report import render_token_report

    toolset = _load(source, mcp, mcp_timeout)
    report = measure_toolset(toolset, tokenizer)
    width, color = _style(sys.stdout) if output is None else (DEFAULT_WIDTH, False)
    _emit(render_token_report(report, fmt.value, width=width, color=color), output)


@app.command()
@_handle_errors
def slim(
    source: SourceArg = None,
    mcp: McpOpt = None,
    mcp_timeout: McpTimeoutOpt = 60.0,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", metavar="FILE", help="Write the slimmed JSON here (default: stdout)."
        ),
    ] = None,
    level: Annotated[
        Level,
        typer.Option(
            "--level",
            "-l",
            help=(
                "safe: same valid calls, only redundant text removed. "
                "annotations: also drop examples and long text."
            ),
        ),
    ] = Level.safe,
    enable: Annotated[
        list[str] | None,
        typer.Option(
            "--enable", "-e", metavar="RULE", help="Turn a rule on (id or name). Repeatable."
        ),
    ] = None,
    disable: Annotated[
        list[str] | None,
        typer.Option(
            "--disable", "-d", metavar="RULE", help="Turn a rule off (id or name). Repeatable."
        ),
    ] = None,
    option: Annotated[
        list[str] | None,
        typer.Option(
            "--option",
            metavar="RULE.KEY=VALUE",
            help="Set a rule option, e.g. SL101.max_chars=200. Repeatable.",
        ),
    ] = None,
    verify: Annotated[
        bool,
        typer.Option(
            "--verify",
            help="Check every changed schema still accepts the same calls; undo any that don't.",
        ),
    ] = False,
    verify_examples: Annotated[
        int,
        typer.Option("--verify-examples", metavar="N", min=1, help="Examples tried per direction."),
    ] = 50,
    fail_on_rollback: Annotated[
        bool,
        typer.Option("--fail-on-rollback", help="Exit 1 if verification undid any change."),
    ] = False,
    tokenizer: TokenizerOpt = None,
    report_fmt: Annotated[
        OutputFormat,
        typer.Option("--report", "-r", help="Report format (the report goes to stderr)."),
    ] = OutputFormat.table,
    report_file: Annotated[
        Path | None,
        typer.Option("--report-file", metavar="FILE", help="Write the report here, not stderr."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Don't print the report.")] = False,
    compact: Annotated[
        bool, typer.Option("--compact", help="Write minified JSON (no indentation).")
    ] = False,
) -> None:
    """Rewrite tool definitions to cost fewer tokens (JSON to stdout, report to stderr)."""
    from toolism.io import dumps
    from toolism.pipeline import slim as slim_toolset
    from toolism.report import render_slim_report
    from toolism.rules import parse_option_assignment

    if fail_on_rollback and not verify:
        raise UsageProblem("--fail-on-rollback needs --verify")
    options: dict[str, dict[str, str]] = {}
    for assignment in option or []:
        rule_key, key, value = parse_option_assignment(assignment)
        options.setdefault(rule_key, {})[key] = value

    toolset = _load(source, mcp, mcp_timeout)
    result = slim_toolset(
        toolset,
        level=level.value,
        enable=_split(enable),
        disable=_split(disable),
        options=options,
        tokenizer=tokenizer,
        verify=verify,
        verify_examples=verify_examples,
    )
    _emit(dumps(result.toolset, indent=None if compact else 2), output)

    if report_file is not None:
        _write_file(report_file, render_slim_report(result.report, report_fmt.value))
    elif not quiet:
        width, color = _style(sys.stderr)
        text = render_slim_report(result.report, report_fmt.value, width=width, color=color)
        typer.echo(text, nl=False, err=True)

    rollbacks = result.report.rollbacks
    if fail_on_rollback and rollbacks:
        names = ", ".join(sorted({r.tool for r in rollbacks}))
        _print_error(f"verification rolled back changes to {len(rollbacks)} tool(s): {names}")
        raise typer.Exit(EXIT_FAILED)


@app.command()
@_handle_errors
def diff(
    source: SourceArg = None,
    mcp: McpOpt = None,
    mcp_timeout: McpTimeoutOpt = 60.0,
    level: Annotated[
        Level,
        typer.Option("--level", "-l", help="safe: redundant text; annotations: also lossy text."),
    ] = Level.safe,
    enable: Annotated[
        list[str] | None,
        typer.Option("--enable", "-e", metavar="RULE", help="Turn a rule on. Repeatable."),
    ] = None,
    disable: Annotated[
        list[str] | None,
        typer.Option("--disable", "-d", metavar="RULE", help="Turn a rule off. Repeatable."),
    ] = None,
    option: Annotated[
        list[str] | None,
        typer.Option("--option", metavar="RULE.KEY=VALUE", help="Set a rule option. Repeatable."),
    ] = None,
    output: OutputOpt = None,
    exit_code: Annotated[
        bool,
        typer.Option("--exit-code", help="Exit 1 if any tool would change, 0 otherwise."),
    ] = False,
) -> None:
    """Preview per-tool changes as a unified diff; omit unchanged tools."""
    from toolism.pipeline import slim as slim_toolset
    from toolism.report import render_tool_diff
    from toolism.rules import parse_option_assignment

    options: dict[str, dict[str, str]] = {}
    for assignment in option or []:
        rule_key, key, value = parse_option_assignment(assignment)
        options.setdefault(rule_key, {})[key] = value
    toolset = _load(source, mcp, mcp_timeout)
    result = slim_toolset(
        toolset,
        level=level.value,
        enable=_split(enable),
        disable=_split(disable),
        options=options,
        tokenizer="heuristic",
    )
    _, color = _style(sys.stdout) if output is None else (DEFAULT_WIDTH, False)
    rendered = render_tool_diff(toolset, result.toolset, color=color)
    _emit(rendered, output, color=color)
    if not rendered:
        typer.echo("No changes.", err=True)
    for warning in result.report.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if exit_code and rendered:
        raise typer.Exit(EXIT_FAILED)


@app.command()
@_handle_errors
def check(
    source: SourceArg = None,
    mcp: McpOpt = None,
    mcp_timeout: McpTimeoutOpt = 60.0,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            "-b",
            metavar="FILE",
            help="Committed baseline to compare with (create it with --update-baseline).",
        ),
    ] = None,
    max_increase: Annotated[
        list[str] | None,
        typer.Option(
            "--max-increase",
            metavar="N|N%",
            help=(
                "Allowed growth over the baseline: tokens (500) or percent (10%). Give both to "
                "allow whichever is larger. Default: no growth."
            ),
        ),
    ] = None,
    max_total: Annotated[
        int | None,
        typer.Option("--max-total", metavar="N", min=0, help="Fail if the total is above N."),
    ] = None,
    max_tool: Annotated[
        int | None,
        typer.Option("--max-tool", metavar="N", min=0, help="Fail if any one tool is above N."),
    ] = None,
    update_baseline: Annotated[
        bool,
        typer.Option("--update-baseline", help="Write the baseline from this run and exit 0."),
    ] = False,
    allow_tokenizer_change: Annotated[
        bool,
        typer.Option(
            "--allow-tokenizer-change",
            help="Don't fail when the baseline used another tokenizer (growth is not checked).",
        ),
    ] = False,
    tokenizer: TokenizerOpt = None,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", "-f", help="Report format.")
    ] = OutputFormat.table,
    output: OutputOpt = None,
    summary_md: Annotated[
        Path | None,
        typer.Option(
            "--summary-md",
            metavar="FILE",
            help="Also write a Markdown summary here. $GITHUB_STEP_SUMMARY is used automatically.",
        ),
    ] = None,
) -> None:
    """Fail when tool definitions cost more tokens than allowed. For CI."""
    from toolism.check import Budget, load_baseline, parse_increase, render_check, run_check
    from toolism.check import save_baseline as write_baseline
    from toolism.measurement import measure as measure_toolset

    if update_baseline and baseline is None:
        raise UsageProblem("--update-baseline needs --baseline FILE")
    pct: float | None = None
    absolute: int | None = None
    for text in max_increase or []:
        try:
            new_pct, new_abs = parse_increase(text)
        except ValueError as exc:
            raise UsageProblem(f"--max-increase: {exc}") from None
        if (new_pct is not None and pct is not None) or (
            new_abs is not None and absolute is not None
        ):
            raise UsageProblem("--max-increase: give at most one percentage and one token count")
        pct = new_pct if new_pct is not None else pct
        absolute = new_abs if new_abs is not None else absolute
    if not update_baseline and baseline is None and max_total is None and max_tool is None:
        raise UsageProblem("nothing to check: pass --baseline and/or --max-total/--max-tool")
    if baseline is None and (pct is not None or absolute is not None):
        raise UsageProblem("--max-increase needs --baseline")

    toolset = _load(source, mcp, mcp_timeout)
    report = measure_toolset(toolset, tokenizer)

    if update_baseline:
        assert baseline is not None
        write_baseline(baseline, report)
        typer.echo(
            f"Wrote {baseline}: {report.total:,} tokens across {report.tool_count} tools "
            f"({report.tokenizer})."
        )
        return

    base = load_baseline(baseline) if baseline is not None else None
    if base is not None and pct is None and absolute is None:
        absolute = 0  # a baseline without an explicit allowance means "must not grow"
    budget = Budget(
        max_increase_pct=pct, max_increase_abs=absolute, max_total=max_total, max_tool=max_tool
    )
    result = run_check(report, base, budget, allow_tokenizer_change=allow_tokenizer_change)

    width, color = _style(sys.stdout) if output is None else (DEFAULT_WIDTH, False)
    _emit(render_check(result, fmt.value, width=width, color=color), output)
    markdown = render_check(result, "markdown")
    if summary_md is not None:
        _write_file(summary_md, markdown)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        _write_file(Path(step_summary), markdown + "\n", append=True)
    if not result.ok:
        raise typer.Exit(EXIT_FAILED)


@app.command()
@_handle_errors
def rules(
    fmt: Annotated[
        OutputFormat,
        typer.Option("--format", "-f", help="table, json, or markdown (the docs/rules.md page)."),
    ] = OutputFormat.table,
    output: OutputOpt = None,
) -> None:
    """List the slimming rules, their safety level and options."""
    from toolism.report import render_rules

    width, color = _style(sys.stdout) if output is None else (DEFAULT_WIDTH, False)
    _emit(render_rules(fmt.value, width=width, color=color), output)


def _utf8_streams() -> None:
    """Pipes on Windows default to the ANSI code page; JSON and reports are UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding != "utf8" and reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # the stream refuses: keep it
                reconfigure(encoding="utf-8")


def main() -> NoReturn:
    """Console-script entry point."""
    _utf8_streams()
    app()
    sys.exit(EXIT_OK)  # pragma: no cover - app() exits itself


if __name__ == "__main__":  # pragma: no cover
    main()
