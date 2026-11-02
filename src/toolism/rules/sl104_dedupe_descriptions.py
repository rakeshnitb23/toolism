"""SL104 dedupe-descriptions — drop property descriptions repeated in the tool description."""

from __future__ import annotations

import re
from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath, property_name

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _norm_unit(text: str) -> str:
    return text.strip().rstrip(".!?").strip()


def _sentences_and_lines(tool_description: str, prop: str) -> set[str]:
    units: set[str] = set()
    prefix = re.compile(
        rf"^(?:[-*•]\s*)?`?{re.escape(prop)}`?\s*(?:[:=\-—]|\bis\b)\s*",
        re.IGNORECASE,
    )
    for raw_line in tool_description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        units.add(_norm_unit(line))
        stripped = prefix.sub("", line, count=1).strip()
        if stripped != line:
            units.add(_norm_unit(stripped))
        for sentence in _SENTENCE_SPLIT.split(line):
            sent = sentence.strip()
            if not sent:
                continue
            units.add(_norm_unit(sent))
            sent_stripped = prefix.sub("", sent, count=1).strip()
            if sent_stripped != sent:
                units.add(_norm_unit(sent_stripped))
    units.discard("")
    return units


@register_rule
class DedupeDescriptions(SchemaRule):
    """Some servers repeat each property's description word for word in the tool description, so
    the model pays for the same text twice. Drop the property-level copy only when it matches a
    whole sentence or line of the tool description."""

    id = "SL104"
    name = "dedupe-descriptions"
    summary = "Drop property descriptions that match a sentence or line of the tool description"
    safety = Safety.LOSSY_ANNOTATIONS

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        description = node.get("description")
        if not isinstance(description, str) or not description.strip():
            return node
        prop = property_name(path)
        if prop is None:
            return node
        tool_description = ctx.tool.description or ""
        if not tool_description:
            return node
        needle = _norm_unit(description)
        if len(needle.split()) < 3:
            return node
        if needle in _sentences_and_lines(tool_description, prop):
            del node["description"]
        return node
