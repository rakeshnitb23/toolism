"""Format-neutral, immutable tool models (design C1).

A :class:`ToolDef` holds the three things toolism rewrites — name, description and input schema —
plus the original item (``source``) so serialization can patch it and keep every other key and the
original key order. A :class:`ToolSet` is an ordered collection of tools plus the document they came
from and where the tool list lives inside it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

__all__ = ["ToolDef", "ToolFormat", "ToolSet", "canonical_json"]


class ToolFormat(str, Enum):
    """The wire formats toolism reads and writes."""

    OPENAI_CHAT = "openai-chat"
    """Chat Completions: ``{"type": "function", "function": {name, description, parameters}}``."""
    OPENAI_RESPONSES = "openai-responses"
    """Responses API (and legacy ``functions=[...]``): ``{name, description, parameters}``."""
    ANTHROPIC = "anthropic"
    """Messages API: ``{name, description, input_schema}``."""
    MCP = "mcp"
    """MCP ``tools/list``: ``{name, description, inputSchema, ...}``."""
    BEDROCK = "bedrock"
    """Bedrock Converse: ``{toolSpec: {name, description, inputSchema: {json: ...}}}``."""

    def __str__(self) -> str:
        return self.value


def canonical_json(obj: Any) -> str:
    """Key-sorted, compact JSON used for fingerprints and equality shortcuts."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class ToolDef:
    """One tool definition, independent of its wire format.

    ``description`` and ``input_schema`` are ``None`` when the key is absent in the source; writing
    ``None`` back removes the key. ``opaque`` tools (provider built-ins such as ``web_search``) are
    passed through untouched and never counted or rewritten.
    """

    name: str
    description: str | None = None
    input_schema: Any = None
    strict: bool = False
    opaque: bool = False
    source: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    _fingerprint: str | None = field(default=None, init=False, compare=False, repr=False)

    def replace(self, **changes: Any) -> ToolDef:
        """Return a copy with ``changes`` applied (``source`` is kept)."""
        return replace(self, **changes)

    def fingerprint(self) -> str:
        """SHA-256 of the canonical JSON of name, description and input schema.

        Independent of key order, so it detects real content changes only.
        """
        cached = self._fingerprint
        if cached is None:
            payload = canonical_json(
                {"name": self.name, "description": self.description, "schema": self.input_schema}
            )
            cached = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            object.__setattr__(self, "_fingerprint", cached)
        return cached

    def render(self) -> str:
        """Compact JSON a model would read for this tool; the input to text tokenizers.

        Keeps key order (models see key order) and omits absent fields. Opaque tools render to
        an empty string.
        """
        if self.opaque:
            return ""
        payload: dict[str, Any] = {"name": self.name}
        if self.description is not None:
            payload["description"] = self.description
        if self.input_schema is not None:
            payload["parameters"] = self.input_schema
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def __hash__(self) -> int:
        return hash(self.fingerprint())


@dataclass(frozen=True, slots=True, eq=False)
class ToolSet:
    """An ordered set of tools and the document they were read from.

    ``path`` locates the tool list inside ``document`` (``()`` means the document *is* the list).
    """

    tools: tuple[ToolDef, ...]
    format: ToolFormat
    document: Any = None
    path: tuple[str | int, ...] = ()
    warnings: tuple[str, ...] = ()

    def replace_tools(self, tools: Sequence[ToolDef]) -> ToolSet:
        """Return a copy holding ``tools`` (same document, path and format)."""
        return replace(self, tools=tuple(tools))

    def function_tools(self) -> tuple[ToolDef, ...]:
        """Tools toolism can measure and rewrite (everything that is not opaque)."""
        return tuple(t for t in self.tools if not t.opaque)

    def __len__(self) -> int:
        return len(self.tools)

    def __iter__(self) -> Iterator[ToolDef]:
        return iter(self.tools)
