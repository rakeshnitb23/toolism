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


@register_rule
class NoopKeywords(SchemaRule):
    """Each of these is exactly the default and changes nothing: ``required: []``,
    ``description: ""``, empty ``$defs``, ``additionalProperties: true`` (and friends),
    ``additionalProperties: {}``, ``minLength: 0``, ``minItems: 0``, ``minProperties: 0``,
    ``uniqueItems: false``, ``items: {}`` / ``items: true``. ``type: ["x"]`` becomes ``"x"``.
    ``properties: {}`` is kept on purpose — OpenAI rejects object schemas without it. Strict
    tools keep ``required`` and ``additional*`` keywords."""

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
        for keyword in _TRUE_IS_DEFAULT:
            if node.get(keyword) is True or node.get(keyword) == {}:
                del node[keyword]
        for keyword in ("minLength", "minItems", "minProperties"):
            if node.get(keyword) == 0:
                del node[keyword]
        if node.get("uniqueItems") is False:
            del node["uniqueItems"]
        items = node.get("items")
        if items is True or (isinstance(items, dict) and not items):
            del node["items"]
        return node
