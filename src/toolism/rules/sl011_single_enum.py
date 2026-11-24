"""SL011 single-enum: replace a singleton enum with const."""

from __future__ import annotations

from typing import Any

from toolism.models import ToolDef
from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import (
    Dialect,
    SchemaPath,
    iter_refs,
    iter_subschemas,
    parse_def_ref,
    uses_dynamic_scope,
)

_KNOWN_URIS = {
    f"{scheme}://json-schema.org/{draft}/schema{fragment}"
    for scheme in ("http", "https")
    for draft in ("draft-06", "draft-07", "draft/2019-09", "draft/2020-12")
    for fragment in ("", "#")
}


@register_rule
class SingleEnum(SchemaRule):
    """A singleton enum and const accept exactly the same JSON values in draft 6
    and later. Preserve siblings and key order, and never overwrite an existing
    const. Strict tools, unknown dialects, nested dialects and potentially affected
    references are left alone. Schemas without a dialect use the project's default.
    """

    id = "SL011"
    name = "single-enum"
    summary = "Replace a single-value enum with const"
    safety = Safety.SAFE

    def apply_tool(self, tool: ToolDef, ctx: RuleContext) -> ToolDef:
        schema = tool.input_schema
        if not isinstance(schema, dict) or ctx.strict or ctx.dialect < Dialect.DRAFT6:
            return tool
        if "$schema" in schema:
            uri = schema["$schema"]
            if not isinstance(uri, str) or uri not in _KNOWN_URIS:
                return tool
        if uses_dynamic_scope(schema):
            return tool
        if any(path and "$schema" in node for path, node in iter_subschemas(schema)):
            return tool
        if any(parse_def_ref(ref) is None for _, ref in iter_refs(schema)):
            return tool
        return super().apply_tool(tool, ctx)

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        values = node.get("enum")
        if "const" in node or not isinstance(values, list) or len(values) != 1:
            return node
        return {
            ("const" if key == "enum" else key): (values[0] if key == "enum" else value)
            for key, value in node.items()
        }
