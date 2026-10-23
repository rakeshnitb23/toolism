from __future__ import annotations

import sys
from typing import Any

import pytest

from toolism.errors import MissingExtraError
from toolism.verify import (
    ORIGINAL_TO_SLIM,
    SLIM_TO_ORIGINAL,
    Verdict,
    check_equivalence,
    verify_available,
)

pytestmark = pytest.mark.skipif(not verify_available(), reason="verify extra not installed")

PYDANTIC_OPTIONAL: dict[str, Any] = {
    "type": "object",
    "title": "searchArguments",
    "properties": {
        "query": {"type": "string", "title": "Query"},
        "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
    },
    "required": ["query"],
}


def test_identical_schemas_short_circuit() -> None:
    result = check_equivalence({"type": "string"}, {"type": "string"}, tool="t")
    assert result.verdict is Verdict.EQUIVALENT
    assert result.tool == "t"
    assert result.method == "identical"


def test_annotation_only_differences_are_proved_without_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import toolism.verify as verify_module

    def no_sampling(*args: Any) -> Any:
        raise AssertionError("must not sample")

    monkeypatch.setattr(verify_module, "_search", no_sampling)
    original = {
        "type": "object",
        "title": "Args",
        "properties": {"title": {"type": "string", "title": "Title", "default": None}},
        "examples": [{"title": "x"}],
    }
    slim = {"type": "object", "properties": {"title": {"type": "string"}}}
    result = check_equivalence(original, slim)
    assert result.verdict is Verdict.EQUIVALENT
    assert result.method == "annotations-only"


def test_property_named_like_an_annotation_is_not_stripped() -> None:
    # Dropping a *property* called "description" is a real change and must be sampled.
    original = {
        "type": "object",
        "properties": {"description": {"type": "string"}},
        "required": ["description"],
    }
    slim = {"type": "object", "properties": {}, "required": ["description"]}
    result = check_equivalence(original, slim)
    assert result.method == "sampled"


def test_none_means_empty_schema() -> None:
    assert check_equivalence(None, {}).verdict is Verdict.EQUIVALENT


def test_safe_rewrite_is_equivalent() -> None:
    slim = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": ["integer", "null"], "default": None},
        },
        "required": ["query"],
    }
    result = check_equivalence(PYDANTIC_OPTIONAL, slim)
    assert result.verdict is Verdict.EQUIVALENT
    assert result.method == "sampled"


def test_dropping_required_is_caught() -> None:
    slim = {"type": "object", "properties": {"query": {"type": "string"}}}
    result = check_equivalence(slim, PYDANTIC_OPTIONAL)
    assert result.verdict is Verdict.COUNTEREXAMPLE
    assert result.direction == ORIGINAL_TO_SLIM
    assert "query" not in result.example  # accepted without 'query', rejected by the other


def test_naive_nullable_collapse_is_caught() -> None:
    naive = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    result = check_equivalence(PYDANTIC_OPTIONAL, naive)
    assert result.verdict is Verdict.COUNTEREXAMPLE
    assert result.example["limit"] is None


def test_counterexample_in_reverse_direction() -> None:
    result = check_equivalence({"type": "integer"}, {"type": "number"})
    assert result.verdict is Verdict.COUNTEREXAMPLE
    assert result.direction == SLIM_TO_ORIGINAL
    assert isinstance(result.example, float)


def test_results_are_deterministic() -> None:
    first = check_equivalence({"type": "integer"}, {"type": "number"})
    second = check_equivalence({"type": "integer"}, {"type": "number"})
    assert first == second


def test_draft07_semantics_are_respected() -> None:
    # Under draft-07 keywords next to $ref are ignored, so these two are NOT equivalent.
    draft7 = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"a": {"$ref": "#/definitions/S", "maxLength": 2}},
        "required": ["a"],
        "definitions": {"S": {"type": "string"}},
    }
    inlined = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"a": {"type": "string", "maxLength": 2}},
        "required": ["a"],
    }
    result = check_equivalence(draft7, inlined)
    assert result.verdict is Verdict.COUNTEREXAMPLE
    assert len(result.example["a"]) > 2


def test_generation_follows_validator_dialect() -> None:
    from toolism.verify import _generation_schema

    draft7 = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {"a": {"$ref": "#/definitions/S", "maxLength": 2}},
        "definitions": {"S": {"type": "string"}},
    }
    assert _generation_schema(draft7) == {
        "properties": {"a": {"$ref": "#/definitions/S"}},
        "definitions": {"S": {"type": "string"}},
    }
    modern = {"properties": {"a": {"$ref": "#/$defs/S", "maxLength": 2}}}
    assert _generation_schema(modern) == modern  # 2020-12: siblings apply, keep them
    assert _generation_schema(True) is True


def test_ungeneratable_schema_is_skipped_not_passed() -> None:
    recursive = {
        "$ref": "#/$defs/Node",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"children": {"type": "array", "items": {"$ref": "#/$defs/Node"}}},
            }
        },
    }
    variant = {**recursive, "maxProperties": 3}  # a structural change, so sampling is needed
    result = check_equivalence(recursive, variant)
    assert result.verdict is Verdict.SKIPPED
    assert "cannot generate" in result.reason


def test_invalid_schema_is_skipped() -> None:
    result = check_equivalence({"type": 5}, {"type": "string"})
    assert result.verdict is Verdict.SKIPPED


def test_boolean_schemas() -> None:
    assert check_equivalence(True, {}).verdict is Verdict.EQUIVALENT
    assert check_equivalence(True, False).verdict is Verdict.COUNTEREXAMPLE


def test_missing_extra_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hypothesis_jsonschema", None)
    assert not verify_available()
    with pytest.raises(MissingExtraError) as info:
        check_equivalence({"type": "string"}, {"type": "integer"})
    assert info.value.extra == "verify"


def test_inputs_are_never_mutated() -> None:
    """hypothesis-jsonschema resolves $refs in place; check_equivalence must work on copies."""
    import copy

    shared = {"type": "object", "properties": {"n": {"type": "string"}}}
    original = {
        "type": "object",
        "title": "Tool",
        "properties": {
            "a": {"type": "array", "items": {"$ref": "#/$defs/X"}},
            "b": {"$ref": "#/$defs/X"},
        },
        "$defs": {"X": shared},
    }
    slim = copy.deepcopy(original)
    del slim["title"]
    slim["properties"]["a"]["minItems"] = 0  # not annotation-only: forces sampling
    before = copy.deepcopy((original, slim))
    result = check_equivalence(original, slim, max_examples=10)
    assert result.method == "sampled"
    assert (original, slim) == before
