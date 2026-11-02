from __future__ import annotations

import copy
from typing import Any

import pytest

from toolism.models import canonical_json
from toolism.schema import (
    DefRef,
    Dialect,
    def_usage,
    defs_name,
    detect_dialect,
    is_recursive,
    iter_refs,
    iter_subschemas,
    known_dialect,
    map_subschemas,
    parse_def_ref,
    property_name,
    ref_siblings_apply,
    uses_dynamic_scope,
)

TRICKY: dict[str, Any] = {
    "type": "object",
    "title": "Root",
    "properties": {
        # properties whose *names* are JSON Schema keywords
        "title": {"type": "string", "title": "Title"},
        "description": {"type": "string"},
        "default": {"type": "integer", "default": 3},
        "$ref": {"type": "string"},
        "type": {"enum": [{"title": "not a schema"}, "b"]},
    },
    "required": ["title"],
    "examples": [{"title": "instance data, never a schema"}],
    "x-vendor": {"title": "unknown keyword, never entered"},
}


def _paths(schema: Any) -> list[tuple[str | int, ...]]:
    return [p for p, _ in iter_subschemas(schema)]


def test_walker_visits_only_subschemas() -> None:
    assert _paths(TRICKY) == [
        (),
        ("properties", "title"),
        ("properties", "description"),
        ("properties", "default"),
        ("properties", "$ref"),
        ("properties", "type"),
    ]


def test_walker_handles_every_keyword_class() -> None:
    schema = {
        "allOf": [{"type": "string"}, True],
        "items": [{"type": "integer"}, {"type": "string"}],  # draft <= 2019 array form
        "additionalItems": False,
        "not": {"type": "null"},
        "if": {"minimum": 1},
        "then": {"maximum": 5},
        "else": {"const": 0},
        "dependencies": {"a": ["b"], "c": {"required": ["d"]}},
        "patternProperties": {"^x": {"type": "number"}},
        "$defs": {"D": {"type": "boolean"}},
        "prefixItems": [{"type": "string"}],
        "contains": {"type": "string"},
    }
    assert _paths(schema) == [
        (),
        ("allOf", 0),
        ("items", 0),
        ("items", 1),
        ("not",),
        ("if",),
        ("then",),
        ("else",),
        ("dependencies", "c"),
        ("patternProperties", "^x"),
        ("$defs", "D"),
        ("prefixItems", 0),
        ("contains",),
    ]


def test_walker_ignores_malformed_positions() -> None:
    schema = {"properties": ["not", "a", "map"], "anyOf": {"not": "a list"}, "items": 3}
    assert _paths(schema) == [()]
    assert _paths(True) == []


def test_map_subschemas_is_post_order_and_does_not_mutate() -> None:
    original = copy.deepcopy(TRICKY)
    order: list[tuple[str | int, ...]] = []

    def strip_titles(node: dict[str, Any], path: tuple[str | int, ...]) -> Any:
        order.append(path)
        node.pop("title", None)
        return node

    result = map_subschemas(TRICKY, strip_titles)
    assert canonical_json(TRICKY) == canonical_json(original)
    assert order[-1] == ()  # parent after children
    assert "title" not in result
    assert "title" not in result["properties"]["title"]
    assert "title" in result["properties"]  # the *property* named title survives
    assert result["examples"] == [{"title": "instance data, never a schema"}]
    assert result["x-vendor"] == {"title": "unknown keyword, never entered"}
    assert result["properties"]["type"]["enum"][0] == {"title": "not a schema"}


def test_map_subschemas_can_replace_nodes_and_keeps_booleans() -> None:
    schema = {"items": {"type": "string"}, "additionalProperties": True, "anyOf": [False, {}]}

    def to_true(node: dict[str, Any], path: tuple[str | int, ...]) -> Any:
        return True if node == {} else node

    assert map_subschemas(schema, to_true) == {
        "items": {"type": "string"},
        "additionalProperties": True,
        "anyOf": [False, True],
    }


def test_property_and_defs_names() -> None:
    assert property_name(("properties", "user_id")) == "user_id"
    assert property_name(("items", "properties", "x")) == "x"
    assert property_name(("patternProperties", "x")) is None
    assert property_name(()) is None
    assert defs_name(("$defs", "Address")) == "Address"
    assert defs_name(("definitions", "A")) == "A"
    assert defs_name(("properties", "A")) is None


# -------------------------------------------------------------------- dialect


@pytest.mark.parametrize(
    ("uri", "dialect"),
    [
        ("http://json-schema.org/draft-04/schema#", Dialect.DRAFT4),
        ("http://json-schema.org/draft-06/schema#", Dialect.DRAFT6),
        ("http://json-schema.org/draft-07/schema#", Dialect.DRAFT7),
        ("https://json-schema.org/draft/2019-09/schema", Dialect.DRAFT2019_09),
        ("https://json-schema.org/draft/2020-12/schema", Dialect.DRAFT2020_12),
    ],
)
def test_detect_dialect(uri: str, dialect: Dialect) -> None:
    assert detect_dialect({"$schema": uri}) is dialect
    assert known_dialect({"$schema": uri}) is dialect


def test_unknown_or_missing_dialect_defaults_to_2020_12() -> None:
    assert detect_dialect({}) is Dialect.DRAFT2020_12
    assert detect_dialect(True) is Dialect.DRAFT2020_12
    assert known_dialect({"$schema": "https://example.com/my-meta"}) is None
    assert detect_dialect({"$schema": "https://example.com/my-meta"}) is Dialect.DRAFT2020_12


@pytest.mark.parametrize(
    ("uri", "known"),
    [
        # json-schema.org URI, but no known marker (draft-03 predates the markers).
        ("http://json-schema.org/draft-03/schema#", None),
        # json-schema.org URI with an unknown path.
        ("https://json-schema.org/draft/2099-99/schema", None),
        # Not a json-schema.org URI at all.
        ("https://example.com/my-meta", None),
        # Known dialect, for contrast.
        ("http://json-schema.org/draft-07/schema#", Dialect.DRAFT7),
    ],
)
def test_unknown_dialect_uris_have_no_known_dialect(uri: str, known: Dialect | None) -> None:
    assert known_dialect({"$schema": uri}) is known
    assert detect_dialect({"$schema": uri}) is (known or Dialect.DRAFT2020_12)


def test_ref_siblings_apply() -> None:
    assert not ref_siblings_apply(Dialect.DRAFT7)
    assert ref_siblings_apply(Dialect.DRAFT2019_09)
    assert ref_siblings_apply(Dialect.DRAFT2020_12)


# ----------------------------------------------------------------------- refs


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("#/$defs/Address", DefRef("$defs", "Address")),
        ("#/definitions/A", DefRef("definitions", "A")),
        ("#/$defs/a~1b~0c", DefRef("$defs", "a/b~c")),
        ("#/$defs/with%20space", DefRef("$defs", "with space")),
        ("#/$defs/100%25", DefRef("$defs", "100%")),
        ("#/$defs/A/properties/x", None),
        ("#/properties/x", None),
        ("other.json#/$defs/A", None),
        ("#anchor", None),
        (5, None),
    ],
)
def test_parse_def_ref(ref: object, expected: DefRef | None) -> None:
    assert parse_def_ref(ref) == expected


def test_defref_pointer_round_trips() -> None:
    ref = DefRef("$defs", "a/b~c")
    assert parse_def_ref(ref.pointer) == ref


SCHEMA_WITH_DEFS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "home": {"$ref": "#/$defs/Address"},
        "work": {"$ref": "#/$defs/Address"},
        "owner": {"$ref": "#/$defs/Person"},
        "tree": {"$ref": "#/$defs/Node"},
        "zip": {"$ref": "#/$defs/Zip/properties/code"},
    },
    "$defs": {
        "Address": {"type": "object", "properties": {"zip": {"$ref": "#/$defs/Zip"}}},
        "Person": {"type": "object"},
        "Node": {"type": "object", "properties": {"children": {"items": {"$ref": "#/$defs/Node"}}}},
        "Zip": {"type": "object", "properties": {"code": {"type": "string"}}},
        "Unused": {"type": "null"},
    },
}


def test_iter_refs() -> None:
    refs = dict(iter_refs(SCHEMA_WITH_DEFS))
    assert refs[("properties", "home")] == "#/$defs/Address"
    assert refs[("$defs", "Node", "properties", "children", "items")] == "#/$defs/Node"


def test_def_usage_counts_pins_and_graph() -> None:
    usage = def_usage(SCHEMA_WITH_DEFS)
    addr, person, node, zip_, unused = (
        DefRef("$defs", n) for n in ("Address", "Person", "Node", "Zip", "Unused")
    )
    assert set(usage.defs) == {addr, person, node, zip_, unused}
    assert usage.counts[addr] == 2
    assert usage.counts[person] == 1
    assert usage.counts[node] == 2  # one outside, one self-reference
    assert usage.counts[zip_] == 1  # from inside Address
    assert usage.counts[unused] == 0
    assert usage.pinned == {zip_}
    assert usage.foreign == ["#/$defs/Zip/properties/code"]
    assert usage.graph[addr] == {zip_}
    assert usage.sites[person] == [("properties", "owner")]


def test_is_recursive() -> None:
    usage = def_usage(SCHEMA_WITH_DEFS)
    assert is_recursive(usage, DefRef("$defs", "Node"))
    assert not is_recursive(usage, DefRef("$defs", "Address"))
    mutual = def_usage(
        {
            "$ref": "#/$defs/A",
            "$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"anyOf": [{"$ref": "#/$defs/A"}, {}]}},
        }
    )
    assert is_recursive(mutual, DefRef("$defs", "A"))
    assert is_recursive(mutual, DefRef("$defs", "B"))


def test_def_usage_container_pointer_pins_everything_in_it() -> None:
    usage = def_usage({"$defs": {"A": {}, "B": {}}, "properties": {"x": {"$ref": "#/$defs"}}})
    assert usage.pinned == {DefRef("$defs", "A"), DefRef("$defs", "B")}


def test_def_usage_on_non_object() -> None:
    assert def_usage(True).defs == {}


@pytest.mark.parametrize(
    ("schema", "dynamic"),
    [
        ({"$id": "https://x/root", "properties": {"a": {"type": "string"}}}, False),
        ({"properties": {"a": {"$id": "https://x/a"}}}, True),
        ({"properties": {"a": {"$anchor": "a"}}}, True),
        ({"$dynamicRef": "#meta"}, True),
        ({"$recursiveRef": "#"}, True),
        (
            {
                "$schema": "http://json-schema.org/draft-04/schema#",
                "properties": {"a": {"id": "x"}},
            },
            True,
        ),
        ({"properties": {"a": {"id": "just an unknown keyword in 2020-12"}}}, False),
    ],
)
def test_uses_dynamic_scope(schema: dict[str, Any], dynamic: bool) -> None:
    assert uses_dynamic_scope(schema) is dynamic
