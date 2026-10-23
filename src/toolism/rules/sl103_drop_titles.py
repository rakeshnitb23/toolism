"""SL103 drop-titles — drop schema ``title`` annotations at the lossy level."""

from __future__ import annotations

from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath


@register_rule
class DropTitles(SchemaRule):
    """``title`` is an annotation keyword, so removing it never changes validation. This lossy
    rule is for users who prefer smaller schemas over retaining display labels. Property names are
    schema keys rather than ``title`` keywords and are therefore never removed."""

    id = "SL103"
    name = "drop-titles"
    summary = "Drop all schema title annotations"
    safety = Safety.LOSSY_ANNOTATIONS

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        node.pop("title", None)
        return node
