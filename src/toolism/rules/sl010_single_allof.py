"""SL010 single-allof — remove a redundant, independent ``allOf`` boundary."""

from __future__ import annotations

from typing import Any

from toolism.models import ToolDef
from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import (
    SchemaPath,
    iter_refs,
    iter_subschemas,
    known_dialect,
    parse_def_ref,
    ref_siblings_apply,
    uses_dynamic_scope,
)

# These keywords read siblings in their own schema object. Bringing members of a
# group together can change validation even when no keyword is duplicated.
_SIBLING_GROUPS = (
    {"properties", "patternProperties", "additionalProperties"},
    {"items", "prefixItems", "additionalItems"},
    {"contains", "minContains", "maxContains"},
    {"if", "then", "else"},
    {"minimum", "exclusiveMinimum"},
    {"maximum", "exclusiveMaximum"},
    {"contentEncoding", "contentMediaType", "contentSchema"},
)
_UNMOVABLE = {"$schema", "$id", "id", "$defs", "definitions"}
_UNEVALUATED = {"unevaluatedProperties", "unevaluatedItems"}
# Unknown vocabularies may define other sibling-dependent keywords.
_KNOWN = {
    "$ref",
    "$comment",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
    "type",
    "enum",
    "const",
    "multipleOf",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "minItems",
    "maxItems",
    "uniqueItems",
    "items",
    "prefixItems",
    "additionalItems",
    "contains",
    "minContains",
    "maxContains",
    "minProperties",
    "maxProperties",
    "required",
    "properties",
    "patternProperties",
    "additionalProperties",
    "propertyNames",
    "dependencies",
    "dependentRequired",
    "dependentSchemas",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
} | _UNMOVABLE


@register_rule
class SingleAllOf(SchemaRule):
    """A single ``allOf`` branch can be merged into its parent when their keywords are
    independent. Duplicate keywords (including annotations) are never overwritten. Groups
    that depend on sibling keywords, such as ``properties``/``additionalProperties`` and
    ``if``/``then``/``else``, must stay on one side of the boundary. Unevaluated keywords,
    unknown keywords and moved definition containers are left alone.

    Strict tools, unknown or nested dialects, dynamic scopes and references other than
    root-definition pointers are skipped. This keeps reference targets and scope intact.
    Under draft <= 7 a branch's ``$ref`` is only hoisted when it gains no siblings; a parent
    ``$ref`` is left alone. Keyword order and all annotation values are preserved."""

    id = "SL010"
    name = "single-allof"
    summary = "Collapse single-branch allOf when merging preserves validation"
    safety = Safety.SAFE

    def apply_tool(self, tool: ToolDef, ctx: RuleContext) -> ToolDef:
        schema = tool.input_schema
        if not isinstance(schema, dict) or ctx.strict or uses_dynamic_scope(schema):
            return tool
        if "$schema" in schema and known_dialect(schema) is None:
            return tool
        for path, node in iter_subschemas(schema):
            if type(node) is not dict or (path and "$schema" in node):
                return tool
        # Removing an allOf changes JSON pointers into that branch. Exact pointers
        # to root definitions keep their targets; other references are conservative skips.
        if any(parse_def_ref(ref) is None for _, ref in iter_refs(schema)):
            return tool
        return super().apply_tool(tool, ctx)

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        branches = node.get("allOf")
        if not isinstance(branches, list) or len(branches) != 1 or type(branches[0]) is not dict:
            return node
        branch = branches[0]
        siblings = node.keys() - {"allOf"}
        if (
            siblings & branch.keys()
            or branch.keys() & _UNMOVABLE
            or (node.keys() | branch.keys()) & _UNEVALUATED
            or not (node.keys() | branch.keys()) <= _KNOWN
            or any(siblings & group and branch.keys() & group for group in _SIBLING_GROUPS)
        ):
            return node
        if not ref_siblings_apply(ctx.dialect) and (
            "$ref" in node or ("$ref" in branch and siblings)
        ):
            return node
        rewritten: dict[str, Any] = {}
        for key, value in node.items():
            if key == "allOf":
                rewritten.update(branch)
            else:
                rewritten[key] = value
        return rewritten
