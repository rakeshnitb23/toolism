from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

from toolism.errors import RuleError
from toolism.formats import parse
from toolism.models import ToolDef, ToolFormat, ToolSet
from toolism.pipeline import select_rules, slim
from toolism.rules import Rule, RuleContext, Safety, SchemaRule, ToolRule
from toolism.schema import SchemaPath
from toolism.tokenizers import HeuristicTokenizer
from toolism.verify import Verdict, verify_available

H = HeuristicTokenizer()

PYDANTIC_TOOL: dict[str, Any] = {
    "name": "search_events",
    "description": "Search events.\n\n    Returns matching events.\n    ",
    "inputSchema": {
        "type": "object",
        "title": "search_eventsArguments",
        "properties": {
            "query": {"type": "string", "title": "Query", "description": "Search query"},
            "limit": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "title": "Limit",
            },
            "project_id": {
                "type": "string",
                "title": "Project Id",
                "description": "The project ID.",
            },
        },
        "required": ["query"],
    },
}


def toolset(*items: dict[str, Any]) -> ToolSet:
    return parse({"tools": list(items)})


# ------------------------------------------------------------------- selection


def test_select_rules_by_level_enable_disable() -> None:
    safe = [r.id for r in select_rules("safe")]
    assert safe == ["SL001", "SL002", "SL003", "SL004", "SL005", "SL006", "SL008", "SL009", "SL010"]
    annotations = [r.id for r in select_rules("annotations")]
    assert annotations == [
        *safe,
        "SL101",
        "SL102",
        "SL103",
    ]  # the level is the gate for lossy rules
    assert "SL101" in [r.id for r in select_rules("safe", enable=["max-description-length"])]
    assert "SL001" not in [r.id for r in select_rules("safe", disable=["SL001"])]
    assert "SL007" in [r.id for r in select_rules(Safety.SAFE, enable=["SL007"])]
    with pytest.raises(RuleError, match="unknown rule"):
        select_rules("safe", enable=["SL999"])


# ------------------------------------------------------------------- end to end


def test_slim_end_to_end_saves_tokens_and_attributes_them() -> None:
    ts = toolset(PYDANTIC_TOOL)
    result = slim(ts, tokenizer=H)
    report = result.report
    (tool,) = result.toolset.tools
    assert tool.description == "Search events.\n\nReturns matching events."
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": ["integer", "null"]},
            "project_id": {"type": "string"},
        },
        "required": ["query"],
    }
    assert report.before > report.after
    assert report.saved == report.before - report.after
    assert sum(r.tokens_saved for r in report.rules) == report.saved
    by_id = {r.rule_id: r for r in report.rules}
    assert by_id["SL001"].tools_changed == ("search_events",)
    assert by_id["SL009"].tokens_saved == 0
    assert report.tokenizer == "heuristic"
    assert report.attribution_tokenizer == "heuristic"
    assert report.level == "safe"
    assert report.tool_count == 1
    assert report.tools[0].saved == report.saved
    assert 0 < report.saved_pct < 100


def test_inputs_are_never_mutated() -> None:
    ts = toolset(PYDANTIC_TOOL)
    fingerprints = [t.fingerprint() for t in ts]
    slim(ts, tokenizer=H)
    assert [t.replace().fingerprint() for t in ts] == fingerprints
    assert ts.tools[0].input_schema["title"] == "search_eventsArguments"


def test_fixpoint_lets_rules_build_on_each_other() -> None:
    # SL004 inlines AddressModel into `address`; only then is its title "Address" redundant.
    ts = toolset(
        {
            "name": "ship",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"$ref": "#/$defs/AddressModel"}},
                "required": ["address"],
                "$defs": {"AddressModel": {"type": "object", "title": "Address"}},
            },
        }
    )
    result = slim(ts, tokenizer=H)
    assert result.toolset.tools[0].input_schema == {
        "type": "object",
        "properties": {"address": {"type": "object"}},
        "required": ["address"],
    }
    assert result.report.passes == 3  # pass 1 inlines, pass 2 drops the title, pass 3 is quiet


def test_opaque_tools_are_uncounted_and_preserved() -> None:
    ts = parse(
        [
            {"name": "a", "input_schema": {"type": "object", "title": "A"}},
            {"type": "web_search_20250305", "name": "web_search"},
        ]
    )
    result = slim(ts, tokenizer=H)
    assert result.report.uncounted == ("web_search",)
    assert result.report.tool_count == 1
    assert result.toolset.tools[1] is ts.tools[1]


def test_empty_toolset() -> None:
    ts = ToolSet(tools=(), format=ToolFormat.MCP)
    report = slim(ts, tokenizer=H).report
    assert (report.before, report.after, report.saved_pct) == (0, 0, 0.0)


def test_rule_options_are_validated() -> None:
    ts = toolset(PYDANTIC_TOOL)
    with pytest.raises(RuleError, match="not enabled"):
        slim(ts, tokenizer=H, options={"SL101": {"max_chars": 10}})
    result = slim(
        ts, tokenizer=H, enable=["SL101"], options={"max-description-length": {"max_chars": 8}}
    )
    query = result.toolset.tools[0].input_schema["properties"]["query"]
    assert query["description"] == "Search…"
    with pytest.raises(RuleError, match="max_passes"):
        slim(ts, tokenizer=H, max_passes=0)


def test_order_rules_run_after_content_rules() -> None:
    ts = parse(
        [
            {"name": "b", "input_schema": {"type": "object", "title": "B"}},
            {"name": "a", "input_schema": {"type": "object", "title": "A"}},
        ]
    )
    result = slim(ts, tokenizer=H, enable=["stable-order"])
    assert [t.name for t in result.toolset.tools] == ["a", "b"]
    assert all("title" not in t.input_schema for t in result.toolset.tools)
    by_id = {r.rule_id: r for r in result.report.rules}
    assert by_id["SL007"].tokens_saved == 0
    assert [t.name for t in result.report.tools] == ["b", "a"]  # per-tool stats keep input order


# ---------------------------------------------------------- remote tokenizers


class FakeRemote:
    name = "remote:fake"
    approximate = False
    local = False

    def __init__(self) -> None:
        self.calls = 0

    def count_tool(self, tool: ToolDef) -> int:
        self.calls += 1
        return len(tool.render())

    def count_tools(self, tools: Any) -> int:
        self.calls += 1
        return sum(len(t.render()) for t in tools)


def test_remote_tokenizer_is_not_used_for_attribution() -> None:
    remote = FakeRemote()
    report = slim(toolset(PYDANTIC_TOOL), tokenizer=remote).report
    assert report.tokenizer == "remote:fake"
    assert report.attribution_tokenizer == "heuristic"
    assert not report.approximate
    assert remote.calls == 4  # before/after per tool + before/after totals, nothing per rule


# ------------------------------------------------------------ contract checks


class DropsATool(Rule):
    id = "SL900"
    name = "drops-a-tool"
    summary = "broken"
    safety = Safety.SAFE

    def apply(self, tools: tuple[ToolDef, ...], options: Mapping[str, Any]) -> tuple[ToolDef, ...]:
        return tools[:-1]


class Renames(ToolRule):
    id = "SL901"
    name = "renames"
    summary = "broken"
    safety = Safety.SAFE

    def apply_tool(self, tool: ToolDef, ctx: RuleContext) -> ToolDef:
        return tool.replace(name=tool.name + "_x")


class Explodes(ToolRule):
    id = "SL902"
    name = "explodes"
    summary = "broken"
    safety = Safety.SAFE

    def apply_tool(self, tool: ToolDef, ctx: RuleContext) -> ToolDef:
        raise KeyError("boom")


class TouchesOpaque(Rule):
    id = "SL903"
    name = "touches-opaque"
    summary = "broken"
    safety = Safety.SAFE

    def apply(self, tools: tuple[ToolDef, ...], options: Mapping[str, Any]) -> tuple[ToolDef, ...]:
        return tuple(t.replace() for t in tools)


class ReorderMakesCopies(Rule):
    id = "SL904"
    name = "reorder-copies"
    summary = "broken"
    safety = Safety.SAFE
    reorders = True

    def apply(self, tools: tuple[ToolDef, ...], options: Mapping[str, Any]) -> tuple[ToolDef, ...]:
        return tuple(t.replace() for t in reversed(tools))


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (DropsATool, "same number of tools"),
        (Renames, "names, order and opaque tools must not change"),
        (Explodes, "SL902 failed: KeyError"),
        (TouchesOpaque, "rewrote opaque tool"),
        (ReorderMakesCopies, "may only reorder"),
    ],
)
def test_rule_contract_violations(rule: type[Rule], message: str) -> None:
    ts = parse(
        [
            {"name": "a", "input_schema": {"type": "object"}},
            {"type": "web_search_20250305", "name": "web_search"},
        ]
    )
    with pytest.raises(RuleError, match=message):
        slim(ts, rules=[rule], tokenizer=H)


class FlipFlop(SchemaRule):
    """Never converges: toggles a marker on every application."""

    id = "SL905"
    name = "flip-flop"
    summary = "never converges"
    safety = Safety.SAFE

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        if not path:
            node["x-flip"] = not node.get("x-flip", False)
        return node


def test_pass_cap_warns() -> None:
    result = slim(toolset(PYDANTIC_TOOL), rules=[FlipFlop], tokenizer=H, max_passes=2)
    assert result.report.passes == 2
    assert any("still changing" in w for w in result.report.warnings)


# --------------------------------------------------------------- verification


class DropsRequired(SchemaRule):
    """An unsafe rule (pretending to be safe): dropping `required` changes validation."""

    id = "SL906"
    name = "drops-required"
    summary = "unsafe"
    safety = Safety.SAFE

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        node.pop("required", None)
        return node


@pytest.mark.skipif(not verify_available(), reason="verify extra not installed")
def test_verification_rolls_back_unsafe_changes() -> None:

    ts = toolset(PYDANTIC_TOOL)
    result = slim(ts, rules=[DropsRequired], tokenizer=H, verify=True)
    report = result.report
    assert result.toolset.tools[0] is ts.tools[0]  # restored
    assert len(report.rollbacks) == 1
    rollback = report.rollbacks[0]
    assert rollback.tool == "search_events"
    assert rollback.rules == ("SL906",)
    assert rollback.reverted == ("SL906",)
    assert "query" not in rollback.example
    assert report.saved == 0
    assert all(r.tokens_saved == 0 for r in report.rules)
    assert report.verification[0].verdict is Verdict.COUNTEREXAMPLE
    assert any("rolled back" in w for w in report.warnings)


@pytest.mark.skipif(not verify_available(), reason="verify extra not installed")
def test_verification_reverts_only_the_unsafe_rule() -> None:
    from toolism.rules import get_rule

    ts = toolset(PYDANTIC_TOOL)
    alone = slim(ts, rules=[get_rule("SL001")], tokenizer=H, verify=True)
    result = slim(ts, rules=[get_rule("SL001"), DropsRequired], tokenizer=H, verify=True)
    report = result.report
    assert result.toolset.tools[0].input_schema == alone.toolset.tools[0].input_schema
    assert result.toolset.tools[0].input_schema["required"] == ["query"]
    [rollback] = report.rollbacks
    assert rollback.rules == ("SL001", "SL906")
    assert rollback.reverted == ("SL906",)
    assert report.saved == alone.report.saved > 0
    stats = {r.rule_id: r for r in report.rules}
    assert stats["SL001"].tokens_saved == alone.report.rules[0].tokens_saved
    assert stats["SL001"].tools_changed == ("search_events",)
    assert stats["SL906"].tokens_saved == 0
    assert stats["SL906"].tools_changed == ()
    assert any("reverted rules SL906" in w for w in report.warnings)


@pytest.mark.skipif(not verify_available(), reason="verify extra not installed")
def test_rollback_falls_back_to_the_original_when_no_culprit_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toolism import pipeline
    from toolism.rules import get_rule

    monkeypatch.setattr(pipeline, "_isolate", lambda *args, **kwargs: (None, ()))
    ts = toolset(PYDANTIC_TOOL)
    result = slim(ts, rules=[get_rule("SL001"), DropsRequired], tokenizer=H, verify=True)
    assert result.toolset.tools[0] is ts.tools[0]
    [rollback] = result.report.rollbacks
    assert rollback.reverted == ("SL001", "SL906")
    assert result.report.saved == 0


@pytest.mark.skipif(not verify_available(), reason="verify extra not installed")
def test_verification_passes_for_safe_rules() -> None:
    result = slim(toolset(PYDANTIC_TOOL), tokenizer=H, verify=True)
    assert not result.report.rollbacks
    assert [v.verdict for v in result.report.verification] == [Verdict.EQUIVALENT]


def test_verify_without_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism import pipeline
    from toolism.errors import MissingExtraError

    monkeypatch.setattr(pipeline, "verify_available", lambda: False)
    with pytest.raises(MissingExtraError):
        slim(toolset(PYDANTIC_TOOL), tokenizer=H, verify=True)


# ---------------------------------------------------------------- performance


def test_thousand_tools_are_fast() -> None:
    items = []
    for i in range(1000):
        item = dict(PYDANTIC_TOOL)
        item["name"] = f"tool_{i}"
        items.append(item)
    ts = toolset(*items)
    start = time.perf_counter()
    report = slim(ts, tokenizer=H).report
    elapsed = time.perf_counter() - start
    assert report.tool_count == 1000
    assert report.saved > 0
    assert elapsed < 5, f"slimming 1000 tools took {elapsed:.1f}s"


@pytest.mark.skipif(not verify_available(), reason="verify extra not installed")
def test_verification_does_not_change_the_output_or_the_input() -> None:
    import copy

    shared = {"type": "object", "properties": {"n": {"type": "string", "title": "N"}}}
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "array", "items": {"$ref": "#/$defs/X"}, "title": "A"},
            "c": {"type": "object", "additionalProperties": True},  # SL008: forces sampling
            "b": {"$ref": "#/$defs/X"},
        },
        "$defs": {"X": shared},
    }
    ts = toolset({"name": "t", "description": "d", "inputSchema": schema})
    pristine = copy.deepcopy(ts.tools[0].input_schema)
    plain = slim(ts, tokenizer=H)
    verified = slim(ts, tokenizer=H, verify=True)
    assert verified.toolset.tools[0].input_schema == plain.toolset.tools[0].input_schema
    assert "$ref" in str(verified.toolset.tools[0].input_schema)  # refs are not expanded
    assert ts.tools[0].input_schema == pristine
    assert verified.report.after == plain.report.after
    assert verified.report.verification[0].method == "sampled"
