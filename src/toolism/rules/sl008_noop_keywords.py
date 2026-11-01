"""SL008 noop-keywords — drop keywords whose value is the default and has no effect."""

from __future__ import annotations

from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath

_TRUE_IS_DEFAULT = (
    "additionalProperties",
    "additionalItems",
    "unevaluatedProperties",
    "unevaluatedItems",
)
_UNEVALUATED = {"unevaluatedProperties", "unevaluatedItems"}
_EVALUATES_LOCATIONS = {*_TRUE_IS_DEFAULT, "items"}


def _restricts_unevaluated(schema: Any) -> bool:
    if isinstance(schema, list):
        return any(_restricts_unevaluated(item) for item in schema)
    if not isinstance(schema, dict):
        return False
    return any(
        (key in _UNEVALUATED and value is not True and value != {}) or _restricts_unevaluated(value)
        for key, value in schema.items()
    )


@register_rule
class NoopKeywords(SchemaRule):
    """Each of these is exactly the default and changes nothing: ``required: []``,
    ``description: ""``, empty ``$defs``, ``additionalProperties: true`` (and friends),
    ``items: {}`` / ``items: true``. ``type: ["x"]`` becomes ``"x"``. ``properties: {}`` is kept on
    purpose — OpenAI rejects object schemas without it. Strict tools keep ``required`` and
    ``additional*`` keywords."""

    id = "SL008"
    name = "noop-keywords"
    summary = "Drop no-op keywords (required: [], additionalProperties: true, ...)"
    safety = Safety.SAFE

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        if node.get("description") == "":
            del node["description"]
        for container in ("$defs", "definitions"):
            if node.get(container) == {}:
                del node[container]
        kind = node.get("type")
        if isinstance(kind, list) and len(kind) == 1 and isinstance(kind[0], str):
            node["type"] = kind[0]
        if ctx.strict:
            return node
        if node.get("required") == []:
            del node["required"]
        # `additionalProperties: true` is not a no-op beside `unevaluatedProperties: false`: it
        # marks properties as evaluated, so removing it would start rejecting them.
        if not _EVALUATES_LOCATIONS.isdisjoint(node) and _restricts_unevaluated(
            ctx.tool.input_schema
        ):
            return node
        for keyword in _TRUE_IS_DEFAULT:
            if node.get(keyword) is True:
                del node[keyword]
        items = node.get("items")
        if items is True or (isinstance(items, dict) and not items):
            del node["items"]
        return node
