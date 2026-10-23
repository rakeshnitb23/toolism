"""SL002 nullable-anyof — ``anyOf: [{type: X}, {type: null}]`` → ``type: [X, "null"]``."""

from __future__ import annotations

from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath

_OVERLAPPING = {frozenset({"integer", "number"})}

# Keywords that only ever constrain strings, so they mean the same on a merged node.
_STRING_ONLY = frozenset({"format", "pattern", "minLength", "maxLength"})


def _branch(branch: Any) -> tuple[list[str], dict[str, Any]] | None:
    """``(types, hoistable)`` of a ``{"type": ...}`` branch, else ``None``.

    ``hoistable`` holds the string-only keywords of a ``{"type": "string", ...}`` branch. A
    branch with any other keyword (or string-only keywords on a non-string type) is rejected.
    """
    if not isinstance(branch, dict) or "type" not in branch:
        return None
    value = branch["type"]
    types = [value] if isinstance(value, str) else value
    if not isinstance(types, list) or not types or not all(isinstance(t, str) for t in types):
        return None
    extras = {k: v for k, v in branch.items() if k != "type"}
    if extras and (value != "string" or not extras.keys() <= _STRING_ONLY):
        return None
    return types, extras


def _overlap(a: list[str], b: list[str]) -> bool:
    return any(x == y or frozenset({x, y}) in _OVERLAPPING for x in a for y in b)


@register_rule
class NullableAnyOf(SchemaRule):
    """``Optional[X]`` in Pydantic becomes ``anyOf`` of two one-keyword branches. A ``type`` array
    accepts exactly the same instances and is shorter. For ``oneOf`` the rewrite is only equivalent
    when no instance can match two branches, so overlapping types (``integer``/``number``) are left
    alone. Note: rewriting to plain ``X`` would reject an explicit ``null`` — never do that.

    ``date | None`` arrives as ``anyOf: [{type: string, format: date}, {type: null}]``. The
    string-only keywords ``format``, ``pattern``, ``minLength`` and ``maxLength`` are hoisted onto
    the merged node, which is equivalent only when exactly one branch is ``type: string`` and no
    other branch can be a string. If the node already carries one of those keywords, or a branch
    has any other keyword, the node is left alone."""

    id = "SL002"
    name = "nullable-anyof"
    summary = "Rewrite anyOf/oneOf of bare {type} branches as a type array"
    safety = Safety.SAFE

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        if ("anyOf" in node) == ("oneOf" in node) or "type" in node:
            return node
        key = "anyOf" if "anyOf" in node else "oneOf"
        branches = node[key]
        if not isinstance(branches, list) or not branches:
            return node
        per_branch: list[list[str]] = []
        hoisted: dict[str, Any] = {}
        for branch in branches:
            parsed = _branch(branch)
            if parsed is None:
                return node
            types, extras = parsed
            if key == "oneOf" and any(_overlap(types, seen) for seen in per_branch):
                return node
            per_branch.append(types)
            if extras:
                if hoisted:  # a second constrained string branch can never be merged
                    return node
                hoisted = extras
        if hoisted and (
            sum("string" in types for types in per_branch) != 1 or hoisted.keys() & node.keys()
        ):
            return node
        merged: list[str] = []
        for types in per_branch:
            merged.extend(t for t in types if t not in merged)
        rewritten: dict[str, Any] = {}
        for name, value in node.items():
            if name == key:
                rewritten["type"] = merged[0] if len(merged) == 1 else merged
                rewritten.update(hoisted)
            else:
                rewritten[name] = value
        return rewritten
