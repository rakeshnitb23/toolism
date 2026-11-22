"""Detect, parse and serialize tool definitions in every supported wire format (design C2).

Serialization *patches a copy of each original item*, so unknown keys, key order and the
surrounding document survive a round trip byte for byte (``serialize(parse(x)) == x``).
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from toolism.errors import FormatError
from toolism.models import ToolDef, ToolFormat, ToolSet

__all__ = ["MAX_INPUT_DEPTH", "detect_item_format", "parse", "serialize"]

# Deepest nesting (dict/list levels) a single tool item may have. Real tool
# schemas nest a handful of levels; the recursive paths that follow parsing
# (deepcopy, the schema walker, canonical JSON) start failing around 400-600
# levels on a default stack, so 200 leaves a wide margin while still rejecting
# the hostile ~3,000-level input from #138 as an input error, not a crash.
MAX_INPUT_DEPTH = 200


@dataclass(frozen=True)
class _Layout:
    """Where the rewritable fields live inside one item of a given format."""

    container: str | None  # None = fields live on the item itself
    schema_key: str
    template: Mapping[str, Any]  # skeleton used when an item is built from scratch


_LAYOUTS: dict[ToolFormat, _Layout] = {
    ToolFormat.OPENAI_CHAT: _Layout("function", "parameters", {"type": "function", "function": {}}),
    ToolFormat.OPENAI_RESPONSES: _Layout(None, "parameters", {"type": "function"}),
    ToolFormat.ANTHROPIC: _Layout(None, "input_schema", {}),
    ToolFormat.MCP: _Layout(None, "inputSchema", {}),
}

# Places a tool list can live, in priority order.
_LIST_PATHS: tuple[tuple[str, ...], ...] = (("tools",), ("result", "tools"), ("functions",))


def _locate(document: Any) -> tuple[tuple[str | int, ...], list[Any]]:
    if isinstance(document, list):
        return (), document
    if isinstance(document, Mapping):
        for path in _LIST_PATHS:
            node: Any = document
            for key in path:
                node = node.get(key) if isinstance(node, Mapping) else None
            if isinstance(node, list):
                return path, node
    raise FormatError(
        "no tool list found",
        hint="expected a JSON array of tools, or an object with 'tools', 'result.tools' "
        "or 'functions'",
    )


def detect_item_format(item: Mapping[str, Any]) -> ToolFormat | None:
    """Return the format of one tool item, or ``None`` for an opaque/built-in tool.

    Raises :class:`FormatError` when the item matches nothing.
    """
    function = item.get("function")
    if isinstance(function, Mapping) and "name" in function:
        return ToolFormat.OPENAI_CHAT
    if "inputSchema" in item:
        return ToolFormat.MCP
    if "input_schema" in item:
        return ToolFormat.ANTHROPIC
    if "name" in item and ("parameters" in item or item.get("type") == "function"):
        return ToolFormat.OPENAI_RESPONSES
    if "type" in item:
        return None  # provider built-in (web_search, bash_..., file_search, ...)
    if "name" in item:
        return ToolFormat.OPENAI_RESPONSES  # legacy functions=[{name, description}]
    raise FormatError(f"unrecognised tool item with keys {sorted(item)}")


def _fields(item: Mapping[str, Any], layout: _Layout) -> Mapping[str, Any]:
    if layout.container is None:
        return item
    inner = item[layout.container]
    assert isinstance(inner, Mapping)
    return inner


def _to_tool(item: Mapping[str, Any], fmt: ToolFormat | None, index: int) -> ToolDef:
    source = copy.deepcopy(dict(item))
    if fmt is None:
        name = item.get("name") or item.get("type") or "<unnamed>"
        return ToolDef(name=str(name), opaque=True, source=source)
    layout = _LAYOUTS[fmt]
    fields = _fields(item, layout)
    name = fields.get("name")
    if not isinstance(name, str):
        raise FormatError(f"tool at index {index} has no string 'name'")
    description = fields.get("description")
    if description is not None and not isinstance(description, str):
        raise FormatError(f"tool {name!r}: 'description' must be a string")
    return ToolDef(
        name=name,
        description=description,
        input_schema=copy.deepcopy(fields.get(layout.schema_key)),
        strict=fields.get("strict") is True,
        source=source,
    )


def _nested_depth(value: Any) -> int:
    """Deepest dict/list nesting in ``value``, computed iteratively.

    Scalars count as 0, a container counts as 1 plus its deepest child. This
    must stay iterative: it runs on untrusted input that may already be too
    deep for any recursive traversal (see #138).
    """
    deepest = 0
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        children: Iterable[Any]
        if isinstance(node, Mapping):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        deepest = max(deepest, depth + 1)
        stack.extend((child, depth + 1) for child in children)
    return deepest


def _check_input_depth(items: list[Any]) -> None:
    """Reject tool items nested deeper than :data:`MAX_INPUT_DEPTH`.

    Runs before any deepcopy, so a hostile schema fails here as a
    :class:`FormatError` instead of surfacing as a ``RecursionError`` from
    deepcopy, the walker, or canonical JSON further down the pipeline.
    """
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        depth = _nested_depth(item)
        if depth > MAX_INPUT_DEPTH:
            raise FormatError(
                f"tool at index {index} is nested too deeply "
                f"({depth} levels, limit {MAX_INPUT_DEPTH})",
                hint="flatten the schema or split it into smaller tools",
            )


def parse(document: Any) -> ToolSet:
    """Parse an already-decoded JSON document into a :class:`ToolSet`."""
    path, items = _locate(document)
    _check_input_depth(items)
    formats: list[ToolFormat | None] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise FormatError(f"tool at index {index} is not a JSON object")
        formats.append(detect_item_format(item))

    seen: dict[ToolFormat, int] = {}
    for index, fmt in enumerate(formats):
        if fmt is not None:
            seen.setdefault(fmt, index)
    if len(seen) > 1:
        described = ", ".join(f"{fmt} (first at index {i})" for fmt, i in seen.items())
        raise FormatError(f"mixed tool formats in one list: {described}")
    toolset_format = next(iter(seen), ToolFormat.OPENAI_RESPONSES)

    tools = tuple(
        _to_tool(item, fmt, i) for i, (item, fmt) in enumerate(zip(items, formats, strict=True))
    )
    counts = Counter(t.name for t in tools if not t.opaque)
    warnings = tuple(f"duplicate tool name {n!r} ({c} times)" for n, c in counts.items() if c > 1)
    return ToolSet(
        tools=tools,
        format=toolset_format,
        document=copy.deepcopy(document),
        path=path,
        warnings=warnings,
    )


def _patch(target: MutableMapping[str, Any], key: str, value: Any) -> None:
    """Assign ``value``. ``None`` removes the key, unless the source held an explicit null."""
    if value is None:
        if key in target and target[key] is not None:
            del target[key]
        return
    target[key] = value


def _to_item(tool: ToolDef, fmt: ToolFormat) -> Any:
    if tool.opaque:
        return copy.deepcopy(dict(tool.source))
    layout = _LAYOUTS[fmt]
    item: dict[str, Any] = copy.deepcopy(dict(tool.source)) if tool.source else {}
    if not item:
        item = copy.deepcopy(dict(layout.template))
    fields: MutableMapping[str, Any]
    if layout.container is None:
        fields = item
    else:
        inner = item.get(layout.container)
        if not isinstance(inner, MutableMapping):
            inner = {}
            item[layout.container] = inner
        fields = inner
    fields["name"] = tool.name
    _patch(fields, "description", tool.description)
    _patch(fields, layout.schema_key, copy.deepcopy(tool.input_schema))
    if tool.strict and fields.get("strict") is not True:
        fields["strict"] = True
    return item


def serialize(toolset: ToolSet) -> Any:
    """Return a new JSON-able document with the (possibly rewritten) tools written back."""
    items = [_to_item(tool, toolset.format) for tool in toolset.tools]
    if not toolset.path or toolset.document is None:
        if toolset.document is None and toolset.path:
            document: Any = {}
            node = document
            for key in toolset.path[:-1]:
                node = node.setdefault(key, {})
            node[toolset.path[-1]] = items
            return document
        return items
    document = copy.deepcopy(toolset.document)
    node = document
    for key in toolset.path[:-1]:
        node = node[key]
    node[toolset.path[-1]] = items
    return document
