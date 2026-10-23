"""SL103 drop-titles — remove every ``title`` annotation (lossy, opt-in)."""

from __future__ import annotations

from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath


@register_rule
class DropTitles(SchemaRule):
    """``title`` is an annotation: it never affects validation. SL001 only drops titles that
    repeat a name the model already sees; this rule drops every title, including informative
    ones, when you opt into the lossy annotations band."""

    id = "SL103"
    name = "drop-titles"
    summary = "Remove title keywords from schemas"
    safety = Safety.LOSSY_ANNOTATIONS

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        # Only the keyword; a property that is literally named "title" lives under properties.
        node.pop("title", None)
        return node
