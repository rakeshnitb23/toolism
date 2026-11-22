from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from toolism.errors import FormatError
from toolism.formats import detect_item_format, parse, serialize
from toolism.io import dump, dumps, load, loads
from toolism.models import ToolDef, ToolFormat, ToolSet

FIXTURES = Path(__file__).parent / "fixtures" / "formats"
FIXTURE_FILES = sorted(FIXTURES.glob("*.json"))


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_round_trip_is_exact(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert serialize(parse(document)) == document


@pytest.mark.parametrize(
    ("name", "fmt", "path", "count", "opaque"),
    [
        ("openai_chat.json", ToolFormat.OPENAI_CHAT, (), 2, 0),
        ("openai_responses.json", ToolFormat.OPENAI_RESPONSES, ("tools",), 2, 1),
        ("anthropic.json", ToolFormat.ANTHROPIC, ("tools",), 2, 1),
        ("mcp_result.json", ToolFormat.MCP, ("result", "tools"), 1, 0),
        ("mcp_tools.json", ToolFormat.MCP, ("tools",), 2, 0),
        ("legacy_functions.json", ToolFormat.OPENAI_RESPONSES, ("functions",), 2, 0),
    ],
)
def test_detects_format_and_location(
    name: str, fmt: ToolFormat, path: tuple[str, ...], count: int, opaque: int
) -> None:
    ts = load(FIXTURES / name)
    assert ts.format is fmt
    assert ts.path == path
    assert len(ts) == count
    assert sum(t.opaque for t in ts) == opaque


def test_fields_are_extracted() -> None:
    ts = load(FIXTURES / "openai_chat.json")
    weather, cities = ts.tools
    assert weather.name == "get_weather"
    assert weather.description == "Get the current weather for a city."
    assert weather.input_schema["required"] == ["city"]
    assert weather.strict is False
    assert cities.description is None
    assert cities.strict is True


def test_opaque_tools_keep_names() -> None:
    responses = load(FIXTURES / "openai_responses.json")
    assert responses.tools[1].name == "web_search_preview"
    anthropic = load(FIXTURES / "anthropic.json")
    assert anthropic.tools[1].name == "web_search"
    assert anthropic.tools[1].opaque


def test_rewritten_fields_are_written_back_in_place() -> None:
    ts = load(FIXTURES / "anthropic.json")
    tool = ts.tools[0]
    new = tool.replace(description="Look up an order.", input_schema={"type": "object"})
    out = serialize(ts.replace_tools([new, ts.tools[1]]))
    item = out["tools"][0]
    assert list(item) == ["name", "description", "input_schema", "cache_control"]
    assert item["description"] == "Look up an order."
    assert item["input_schema"] == {"type": "object"}
    assert item["cache_control"] == {"type": "ephemeral"}
    assert out["messages"] == [{"role": "user", "content": "Where is order 42?"}]


def test_removed_description_deletes_the_key() -> None:
    ts = load(FIXTURES / "openai_chat.json")
    new = ts.tools[0].replace(description=None)
    out = serialize(ts.replace_tools([new, ts.tools[1]]))
    assert "description" not in out[0]["function"]


def test_explicit_null_description_survives() -> None:
    ts = load(FIXTURES / "mcp_tools.json")
    assert ts.tools[1].description is None
    out = serialize(ts)
    assert out["tools"][1]["description"] is None


def test_serialize_does_not_mutate_toolset_document() -> None:
    ts = load(FIXTURES / "mcp_tools.json")
    before = json.dumps(ts.document, sort_keys=True)
    serialize(ts.replace_tools([t.replace(description="x") for t in ts.tools]))
    assert json.dumps(ts.document, sort_keys=True) == before


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (
            ToolFormat.OPENAI_CHAT,
            {
                "type": "function",
                "function": {"name": "t", "description": "d", "parameters": {}, "strict": True},
            },
        ),
        (
            ToolFormat.OPENAI_RESPONSES,
            {"type": "function", "name": "t", "description": "d", "parameters": {}, "strict": True},
        ),
        (
            ToolFormat.ANTHROPIC,
            {"name": "t", "description": "d", "input_schema": {}, "strict": True},
        ),
        (ToolFormat.MCP, {"name": "t", "description": "d", "inputSchema": {}, "strict": True}),
    ],
)
def test_programmatic_toolset_serializes_from_templates(
    fmt: ToolFormat, expected: dict[str, object]
) -> None:
    ts = ToolSet(
        tools=(ToolDef(name="t", description="d", input_schema={}, strict=True),), format=fmt
    )
    assert serialize(ts) == [expected]


def test_programmatic_toolset_with_path_builds_envelope() -> None:
    ts = ToolSet(tools=(ToolDef(name="t"),), format=ToolFormat.MCP, path=("result", "tools"))
    assert serialize(ts) == {"result": {"tools": [{"name": "t"}]}}


def test_mixed_formats_are_rejected() -> None:
    doc = [
        {"name": "a", "input_schema": {}},
        {"type": "function", "function": {"name": "b"}},
    ]
    with pytest.raises(FormatError, match="mixed tool formats"):
        parse(doc)


def test_all_opaque_list_parses() -> None:
    ts = parse([{"type": "web_search_preview"}, {"type": "file_search"}])
    assert all(t.opaque for t in ts)
    assert serialize(ts) == [{"type": "web_search_preview"}, {"type": "file_search"}]


def test_duplicate_names_produce_warning() -> None:
    ts = parse([{"name": "a", "input_schema": {}}, {"name": "a", "input_schema": {}}])
    assert ts.warnings == ("duplicate tool name 'a' (2 times)",)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"model": "x"}, "no tool list found"),
        ("just a string", "no tool list found"),
        ([1, 2], "not a JSON object"),
        ([{"foo": 1}], "unrecognised tool item"),
        ([{"name": 3, "input_schema": {}}], "no string 'name'"),
        ([{"name": "a", "description": 5, "input_schema": {}}], "must be a string"),
    ],
)
def test_invalid_documents(document: object, message: str) -> None:
    with pytest.raises(FormatError, match=message):
        parse(document)


def test_detect_item_format_cases() -> None:
    assert detect_item_format({"type": "function", "function": {"name": "a"}}) is (
        ToolFormat.OPENAI_CHAT
    )
    assert detect_item_format({"name": "a", "inputSchema": {}, "parameters": {}}) is ToolFormat.MCP
    assert detect_item_format({"type": "function", "name": "a"}) is ToolFormat.OPENAI_RESPONSES
    assert detect_item_format({"type": "bash_20250124", "name": "bash"}) is None


# --------------------------------------------------------------------------- io


def test_loads_reports_json_error_position() -> None:
    with pytest.raises(FormatError, match="line 2, column 1"):
        loads('{"tools": [\n}')


def test_loads_rejects_nan() -> None:
    with pytest.raises(FormatError, match="NaN"):
        loads('[{"name": "a", "input_schema": {"default": NaN}}]')


def _deeply_nested_tool(levels: int) -> list[dict[str, object]]:
    schema: object = {"type": "string"}
    for _ in range(levels):
        schema = {"type": "object", "properties": {"a": schema}}
    return [{"name": "t", "inputSchema": schema}]


def test_parse_rejects_deeply_nested_schema_as_input_error() -> None:
    # #138: a ~3,000-level schema used to crash with RecursionError from
    # deepcopy; it must now be a plain FormatError, before any recursion.
    with pytest.raises(FormatError, match="nested too deeply"):
        parse(_deeply_nested_tool(3000))


def _deeply_nested_tool_text(levels: int) -> str:
    # Build the JSON text iteratively: json.dumps() itself recurses and
    # would blow the stack on this input before loads() ever runs.
    text = '{"type": "string"}'
    for _ in range(levels):
        text = '{"type": "object", "properties": {"a": ' + text + "}}"
    return '[{"name": "t", "inputSchema": ' + text + "}]"


def test_loads_rejects_deeply_nested_schema_as_input_error() -> None:
    with pytest.raises(FormatError, match="nested too deeply"):
        loads(_deeply_nested_tool_text(3000))


def test_parse_accepts_moderately_nested_schema() -> None:
    assert len(parse(_deeply_nested_tool(50))) == 1


def test_load_from_file_object_and_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (FIXTURES / "mcp_tools.json").read_text(encoding="utf-8")
    assert len(load(io.StringIO(text))) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert len(load("-")) == 2


def test_load_errors(tmp_path: Path) -> None:
    with pytest.raises(FormatError, match="file not found"):
        load(tmp_path / "missing.json")
    with pytest.raises(FormatError, match="directory"):
        load(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(FormatError, match="UTF-8"):
        load(bad)


def test_unreadable_file_is_a_format_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")

    def denied(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(FormatError, match=r"cannot read .*locked\.json: Permission denied"):
        load(locked)


def test_dumps_pretty_and_compact_and_unicode() -> None:
    ts = parse([{"name": "café", "input_schema": {"type": "object"}}])
    assert (
        dumps(ts) == "[\n  {\n"
        '    "name": "café",\n    "input_schema": {\n      "type": "object"\n    }\n'
        "  }\n]\n"
    )
    assert dumps(ts, indent=None) == '[{"name":"café","input_schema":{"type":"object"}}]\n'


def test_dump_targets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ts = parse([{"name": "a", "input_schema": {}}])
    out = tmp_path / "out.json"
    dump(ts, out)
    assert json.loads(out.read_text(encoding="utf-8")) == [{"name": "a", "input_schema": {}}]
    buf = io.StringIO()
    dump(ts, buf, indent=None)
    assert buf.getvalue() == '[{"name":"a","input_schema":{}}]\n'
    dump(ts, "-", indent=None)
    assert capsys.readouterr().out == '[{"name":"a","input_schema":{}}]\n'
