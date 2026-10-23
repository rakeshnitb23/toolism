"""Render measure and slim reports as a terminal table, GitHub Markdown or versioned JSON (C9)."""

from __future__ import annotations

import inspect
import io
import json
from collections.abc import Sequence
from typing import Any, Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text

from toolism.measurement import TokenReport
from toolism.pipeline import SlimReport
from toolism.rules import Rule, Safety, all_rules
from toolism.verify import Verdict

__all__ = [
    "REPORT_VERSION",
    "Format",
    "render_rules",
    "render_slim_report",
    "render_token_report",
    "rule_to_dict",
    "slim_report_to_dict",
    "token_report_to_dict",
]

REPORT_VERSION = 1
TOP_N = 25
Format = Literal["table", "json", "markdown"]


def _n(value: int) -> str:
    return f"{value:,}"


def _signed(value: int) -> str:
    return f"−{value:,}" if value > 0 else f"+{-value:,}" if value < 0 else "0"


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _tokenizer_note(name: str, approximate: bool) -> str:
    return f"{name} (approximate)" if approximate else name


def _console_render(renderables: Sequence[Any], width: int, color: bool) -> str:
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=width,
        color_system="standard" if color else None,
        force_terminal=color,
        highlight=False,
        soft_wrap=False,
    )
    for item in renderables:
        console.print(item)
    return buffer.getvalue()


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]], align: str) -> str:
    sep = ["---:" if a == "r" else "---" for a in align]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


# ------------------------------------------------------------------- measure


def token_report_to_dict(report: TokenReport) -> dict[str, Any]:
    return {
        "toolism_report": REPORT_VERSION,
        "kind": "measure",
        "tokenizer": report.tokenizer,
        "approximate": report.approximate,
        "total_tokens": report.total,
        "tool_count": report.tool_count,
        "average_tokens": round(report.average, 2),
        "tools": [
            {"name": t.name, "tokens": t.tokens, "percent": _pct(t.tokens, report.total)}
            for t in report.tools
        ],
        "uncounted": list(report.uncounted),
    }


def render_token_report(
    report: TokenReport, fmt: Format = "table", *, width: int = 100, color: bool = False
) -> str:
    if fmt == "json":
        return json.dumps(token_report_to_dict(report), indent=2, ensure_ascii=False) + "\n"
    ranked = sorted(report.tools, key=lambda t: (-t.tokens, t.name))
    shown, hidden = ranked[:TOP_N], ranked[TOP_N:]
    summary = (
        f"{_n(report.total)} tokens across {report.tool_count} tools "
        f"(average {report.average:,.0f}) — {_tokenizer_note(report.tokenizer, report.approximate)}"
    )
    notes = []
    if hidden:
        notes.append(f"… {len(hidden)} more tools ({_n(sum(t.tokens for t in hidden))} tokens)")
    if report.uncounted:
        notes.append(f"Not counted (provider built-ins): {', '.join(report.uncounted)}")

    if fmt == "markdown":
        rows = [
            [f"`{t.name}`", _n(t.tokens), f"{_pct(t.tokens, report.total):.1f}%"] for t in shown
        ]
        parts = ["### toolism — tool definition tokens", "", summary, ""]
        parts.append(_md_table(["Tool", "Tokens", "% of total"], rows, "lrr"))
        parts += ["", *notes] if notes else []
        return "\n".join(parts) + "\n"

    table = Table(title="Tool definition tokens", title_justify="left")
    table.add_column("Tool")
    table.add_column("Tokens", justify="right")
    table.add_column("% of total", justify="right")
    for t in shown:
        table.add_row(t.name, _n(t.tokens), f"{_pct(t.tokens, report.total):.1f}%")
    return _console_render([table, Text(summary), *map(Text, notes)], width, color)


# ---------------------------------------------------------------------- slim


def _verification_summary(report: SlimReport) -> str | None:
    if not report.verification:
        return None
    verdicts = [v.verdict for v in report.verification]
    proved = sum(
        1
        for v in report.verification
        if v.verdict is Verdict.EQUIVALENT and v.method in ("identical", "annotations-only")
    )
    equivalent = verdicts.count(Verdict.EQUIVALENT)
    counterexamples = verdicts.count(Verdict.COUNTEREXAMPLE)
    rolled_back = " (rolled back)" if counterexamples else ""
    return (
        f"Verification: {equivalent} equivalent ({proved} proved, {equivalent - proved} sampled), "
        f"{verdicts.count(Verdict.SKIPPED)} skipped, {counterexamples} counterexamples{rolled_back}"
    )


def slim_report_to_dict(report: SlimReport) -> dict[str, Any]:
    return {
        "toolism_report": REPORT_VERSION,
        "kind": "slim",
        "tokenizer": report.tokenizer,
        "approximate": report.approximate,
        "attribution_tokenizer": report.attribution_tokenizer,
        "level": report.level,
        "tool_count": report.tool_count,
        "before_tokens": report.before,
        "after_tokens": report.after,
        "saved_tokens": report.saved,
        "saved_percent": round(report.saved_pct, 2),
        "passes": report.passes,
        "rules": [
            {
                "id": r.rule_id,
                "name": r.name,
                "tokens_saved": r.tokens_saved,
                "tools_changed": list(r.tools_changed),
            }
            for r in report.rules
        ],
        "tools": [
            {"name": t.name, "before": t.before, "after": t.after, "saved": t.saved}
            for t in report.tools
        ],
        "uncounted": list(report.uncounted),
        "warnings": list(report.warnings),
        "rollbacks": [
            {
                "tool": r.tool,
                "rules": list(r.rules),
                "reverted": list(r.reverted),
                "direction": r.direction,
                "example": r.example,
            }
            for r in report.rollbacks
        ],
        "verification": [
            {
                "tool": v.tool,
                "verdict": v.verdict.value,
                "method": v.method,
                "direction": v.direction,
                "reason": v.reason,
            }
            for v in report.verification
        ],
    }


def render_slim_report(
    report: SlimReport, fmt: Format = "table", *, width: int = 100, color: bool = False
) -> str:
    if fmt == "json":
        return (
            json.dumps(slim_report_to_dict(report), indent=2, ensure_ascii=False, default=str)
            + "\n"
        )
    headline = (
        f"Slimmed {report.tool_count} tools: {_n(report.before)} → {_n(report.after)} tokens "
        f"({_signed(report.saved)}, −{report.saved_pct:.1f}%) — "
        f"{_tokenizer_note(report.tokenizer, report.approximate)}, level {report.level}"
    )
    rules = [r for r in report.rules if r.tokens_saved or r.tools_changed]
    changed = sorted((t for t in report.tools if t.saved), key=lambda t: (-t.saved, t.name))
    shown = changed[:TOP_N]
    notes = [f"Warning: {w}" for w in report.warnings]
    verification = _verification_summary(report)
    if verification:
        notes.append(verification)
    if report.uncounted:
        notes.append(f"Not counted (provider built-ins): {', '.join(report.uncounted)}")
    if report.attribution_tokenizer != report.tokenizer:
        notes.append(f"Per-rule savings estimated with {report.attribution_tokenizer}.")

    if fmt == "markdown":
        parts = ["### toolism — slim report", "", headline, ""]
        if rules:
            rule_rows = [
                [f"`{r.rule_id}`", r.name, _signed(r.tokens_saved), str(len(r.tools_changed))]
                for r in rules
            ]
            parts += [_md_table(["Rule", "Name", "Tokens", "Tools"], rule_rows, "llrr"), ""]
        if shown:
            tool_rows = [
                [f"`{t.name}`", _n(t.before), _n(t.after), _signed(t.saved)] for t in shown
            ]
            parts += [_md_table(["Tool", "Before", "After", "Saved"], tool_rows, "lrrr"), ""]
        parts += notes
        return "\n".join(parts).rstrip() + "\n"

    renderables: list[Any] = [Text(headline)]
    if rules:
        rule_table = Table(title="Savings by rule", title_justify="left")
        for column, justify in (
            ("Rule", "left"),
            ("Name", "left"),
            ("Tokens", "right"),
            ("Tools", "right"),
        ):
            rule_table.add_column(column, justify=justify)  # type: ignore[arg-type]
        for r in rules:
            rule_table.add_row(
                r.rule_id, r.name, _signed(r.tokens_saved), str(len(r.tools_changed))
            )
        renderables.append(rule_table)
    if shown:
        tool_table = Table(title="Most reduced tools", title_justify="left")
        for column, justify in (
            ("Tool", "left"),
            ("Before", "right"),
            ("After", "right"),
            ("Saved", "right"),
        ):
            tool_table.add_column(column, justify=justify)  # type: ignore[arg-type]
        for t in shown:
            tool_table.add_row(t.name, _n(t.before), _n(t.after), _signed(t.saved))
        renderables.append(tool_table)
    if len(changed) > TOP_N:
        notes.insert(0, f"… {len(changed) - TOP_N} more changed tools")
    renderables += [Text(note) for note in notes]
    return _console_render(renderables, width, color)


# --------------------------------------------------------------------- rules


def _enabled_by(rule: type[Rule]) -> str:
    """How a user turns the rule on: by a ``--level`` or only with ``--enable``."""
    if not rule.default_enabled:
        return f"--enable {rule.id}"
    if rule.safety is Safety.SAFE:
        return "default"
    return f"--level {rule.safety.label}"


def _md_code(text: str) -> str:
    return f"`{text}`" if text.startswith("-") else text


def _rule_doc(rule: type[Rule]) -> str:
    return inspect.cleandoc(rule.__doc__ or "").replace("``", "`")


def rule_to_dict(rule: type[Rule]) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "safety": rule.safety.label,
        "default_enabled": rule.default_enabled,
        "enabled_by": _enabled_by(rule),
        "reorders": rule.reorders,
        "summary": rule.summary,
        "description": " ".join(_rule_doc(rule).split()),
        "options": [
            {"name": name, "type": spec.type.__name__, "default": spec.default, "help": spec.help}
            for name, spec in rule.option_specs.items()
        ],
    }


_SAFETY_TEXT = {
    Safety.SAFE: "Validation-equivalent; removes only provably redundant information.",
    Safety.LOSSY_ANNOTATIONS: (
        "Validation-equivalent, but may drop annotation text the model could use."
    ),
    Safety.CHANGES_VALIDATION: "May change which tool calls validate.",
}


def _rules_markdown(rules: Sequence[type[Rule]]) -> str:
    rows = [
        [f"[`{r.id}`](#{r.id.lower()}-{r.name})", r.name, r.safety.label, _md_code(_enabled_by(r))]
        for r in rules
    ]
    parts = [
        "# Rules",
        "",
        "<!-- Generated by `toolism rules --format markdown`. Do not edit by hand. -->",
        "",
        "Rules run in id order. `SL0xx` rules are **safe**: they never change which tool calls",
        "validate and only drop information the model already has. `SL1xx` rules are lossy for",
        "annotations and run with `--level annotations`. Turn any rule on or off with `--enable` /",
        "`--disable` (by id or name), and set options with `--option RULE.key=value`.",
        "",
        _md_table(["Rule", "Name", "Safety", "Enabled by"], rows, "llll"),
    ]
    for r in rules:
        parts += ["", f"## {r.id} {r.name}", "", f"**{r.summary}.**", "", _rule_doc(r)]
        parts += ["", f"- Safety: `{r.safety.label}` — {_SAFETY_TEXT[r.safety]}"]
        parts += [f"- Enabled by: {_md_code(_enabled_by(r))}"]
        if r.reorders:
            parts.append("- Reorders tools; applied after all content rules.")
        if r.option_specs:
            parts += ["", "| Option | Type | Default | Meaning |", "| --- | --- | --- | --- |"]
            parts += [
                f"| `{name}` | {spec.type.__name__} | `{json.dumps(spec.default)}` | {spec.help} |"
                for name, spec in r.option_specs.items()
            ]
    return "\n".join(parts) + "\n"


def render_rules(
    fmt: Format = "table",
    *,
    rules: Sequence[type[Rule]] | None = None,
    width: int = 100,
    color: bool = False,
) -> str:
    """List rules as a terminal table, JSON, or the Markdown page published as ``docs/rules.md``."""
    chosen = list(all_rules() if rules is None else rules)
    if fmt == "json":
        payload = {
            "toolism_report": REPORT_VERSION,
            "kind": "rules",
            "rules": [rule_to_dict(r) for r in chosen],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if fmt == "markdown":
        return _rules_markdown(chosen)
    table = Table(title="toolism rules", title_justify="left")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Safety")
    table.add_column("Enabled by")
    table.add_column("What it does")
    for r in chosen:
        table.add_row(r.id, r.name, r.safety.label, _enabled_by(r), r.summary)
    hint = Text("Options: toolism rules --format json; docs: docs/rules.md")
    return _console_render([table, hint], width, color)
