"""Generated invariants for every registered rule (issue #147).

A deterministic bounded matrix covers nested schemas, local refs, compositions and
multiple JSON Schema drafts. Equivalence is sampled in *both* directions by toolism's
own verifier: a sampled result is evidence, not a mathematical proof.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from toolism.models import ToolDef
from toolism.rules import Safety, all_rules, resolve_options
from toolism.verify import Verdict, check_equivalence

_DRAFTS = (
    ("http://json-schema.org/draft-07/schema#", "definitions"),
    ("https://json-schema.org/draft/2019-09/schema", "$defs"),
    ("https://json-schema.org/draft/2020-12/schema", "$defs"),
)

_ATOMIC = st.sampled_from(
    (
        {"type": "string", "title": "Value", "minLength": 0},
        {"type": "string", "description": "a generated property value"},
        {"type": "integer", "minimum": 0},
        {"type": "number", "maximum": 10},
        {"type": "null"},
        {"type": ["string"]},
        {"type": "array", "items": {}, "minItems": 0},
        {"type": "object", "properties": {"alpha": {"type": "string", "default": None}}},
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        {"allOf": [{"type": "string", "title": "Text"}]},
        True,
        False,
    )
)

_NESTED = st.recursive(
    _ATOMIC,
    lambda child: st.one_of(
        st.lists(child, min_size=1, max_size=2).map(lambda parts: {"anyOf": parts}),
        st.lists(child, min_size=1, max_size=2).map(lambda parts: {"allOf": parts}),
        st.tuples(child, child).map(
            lambda pair: {
                "type": "object",
                "properties": {"alpha": pair[0], "beta": pair[1]},
                "required": [],
                "additionalProperties": True,
            }
        ),
        child.map(lambda part: {"type": "array", "items": part, "minItems": 0}),
    ),
    max_leaves=7,
)


@st.composite
def _schemas(draw: st.DrawFn) -> dict:
    """Generate bounded valid schemas for known drafts, incl. local JSON pointers."""
    draft, defs_key = draw(st.sampled_from(_DRAFTS))
    nested = copy.deepcopy(draw(_NESTED))
    shape = draw(st.sampled_from(("nested", "reference", "nullable", "allof")))
    if shape == "reference":
        shared = copy.deepcopy(draw(_ATOMIC))
        properties = {
            "alpha": {"$ref": f"#/{defs_key}/Shared"},
            "beta": nested,
        }
        if draw(st.booleans()):
            properties["alpha"]["description"] = "an annotated reference"
        schema = {
            "type": "object",
            defs_key: {"Shared": shared},
            "properties": properties,
            "required": draw(st.sampled_from(([], ["alpha"]))),
        }
    elif shape == "nullable":
        schema = {
            "type": "object",
            "properties": {
                "alpha": {
                    "anyOf": [
                        {"type": "string", "minLength": 0},
                        {"type": "null"},
                    ],
                    "default": None,
                },
                "beta": nested,
            },
            "required": draw(st.sampled_from(([], ["beta"]))),
        }
    elif shape == "allof":
        schema = {"allOf": [nested], "title": "Generated"}
    else:
        schema = {"type": "object", "properties": {"alpha": nested}, "required": []}

    if draw(st.booleans()):
        schema["$schema"] = draft
    return schema


_DESCRIPTIONS = st.sampled_from(
    (
        "",
        "alpha: a generated property value.",
        "Tool arguments are checked before execution.",
        "A generated description. " * 22,
    )
)


@pytest.fixture(scope="module")
def generated_cases() -> list[tuple[dict, bool, str]]:
    """One seeded Hypothesis corpus, then explicit coverage of all draft/shape pairs."""
    cases: list[tuple[dict, bool, str]] = []

    @settings(max_examples=36, derandomize=True, database=None, deadline=None)
    @seed(20261108)
    @given(st.tuples(_schemas(), st.booleans(), _DESCRIPTIONS))
    def collect(sample: tuple[dict, bool, str]) -> None:
        cases.append(copy.deepcopy(sample))

    collect()

    for draft, defs_key in _DRAFTS:
        cases.extend(
            [
                (
                    {
                        "$schema": draft,
                        "type": "object",
                        "properties": {
                            "alpha": {
                                "type": "object",
                                "properties": {"beta": {"type": ["string"]}},
                            }
                        },
                    },
                    False,
                    "",
                ),
                (
                    {
                        "$schema": draft,
                        "type": "object",
                        defs_key: {"Shared": {"type": "string", "title": "Shared"}},
                        "properties": {"alpha": {"$ref": f"#/{defs_key}/Shared"}},
                    },
                    False,
                    "a generated property value",
                ),
                (
                    {
                        "$schema": draft,
                        "type": "object",
                        "properties": {"alpha": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
                    },
                    False,
                    "alpha: a generated property value.",
                ),
                (
                    {
                        "$schema": draft,
                        "type": "object",
                        "properties": {"alpha": {"allOf": [{"type": "integer"}]}},
                    },
                    False,
                    "A generated description. " * 22,
                ),
            ]
        )
    return cases


def test_generated_corpus_has_required_shapes(generated_cases) -> None:
    """Do not silently lose any coverage family when the strategy changes."""
    schemas = [item[0] for item in generated_cases]
    drafts = {schema.get("$schema") for schema in schemas}
    assert {draft for draft, _ in _DRAFTS} <= drafts
    rendered = [str(schema) for schema in schemas]
    assert any("$ref" in sample for sample in rendered)
    assert any("anyOf" in sample for sample in rendered)
    assert any("allOf" in sample for sample in rendered)
    assert any("properties" in sample and "beta" in sample for sample in rendered)


@pytest.mark.parametrize("rule_cls", all_rules(), ids=lambda cls: cls.id)
def test_rule_generated_invariants(rule_cls, generated_cases) -> None:
    """Every registered rule: nonmutation, idempotence, and identity on no-op."""
    for schema, strict, description in generated_cases:
        original = ToolDef(
            name="synthetic_tool",
            description=description,
            input_schema=copy.deepcopy(schema),
            strict=strict,
        )
        snapshot = copy.deepcopy((original.description, original.input_schema))
        options = resolve_options(
            rule_cls,
            {"max_chars": 32, "include_tool": True} if rule_cls.id == "SL101" else None,
        )
        (result,) = rule_cls().apply((original,), options)

        assert (original.description, original.input_schema) == snapshot, rule_cls.id
        (again,) = rule_cls().apply((result,), options)
        assert result.fingerprint() == again.fingerprint(), f"not idempotent: {rule_cls.id}"
        assert (original.description, original.input_schema) == snapshot, rule_cls.id

        if (result.description, result.input_schema) == snapshot:
            assert result is original, f"unchanged object was copied: {rule_cls.id}"


@pytest.mark.slow
@pytest.mark.parametrize(
    "rule_cls",
    [cls for cls in all_rules() if cls.safety is Safety.SAFE and not cls.reorders],
    ids=lambda cls: cls.id,
)
def test_generated_safe_equivalence(rule_cls, generated_cases) -> None:
    """Check changed schemas bidirectionally; report SKIPPED as inconclusive."""
    verdicts = {Verdict.EQUIVALENT: 0, Verdict.SKIPPED: 0}
    for schema, strict, description in generated_cases:
        original = ToolDef(
            name="synthetic_tool",
            description=description,
            input_schema=copy.deepcopy(schema),
            strict=strict,
        )
        (result,) = rule_cls().apply((original,), resolve_options(rule_cls))
        if result.input_schema == original.input_schema:
            continue
        verdict = check_equivalence(original.input_schema, result.input_schema, max_examples=4)
        assert verdict.verdict is not Verdict.COUNTEREXAMPLE, (
            f"{rule_cls.id}: counterexample on {original.input_schema!r} "
            f"-> {result.input_schema!r}: {verdict}"
        )
        if verdict.verdict is Verdict.SKIPPED:
            assert verdict.reason, f"{rule_cls.id}: unexplained verification skip"
        verdicts[verdict.verdict] += 1

    if sum(verdicts.values()):
        assert verdicts[Verdict.EQUIVALENT] > 0, f"{rule_cls.id}: no verified samples"
    print(
        f"{rule_cls.id}: sampled-equivalent={verdicts[Verdict.EQUIVALENT]}, "
        f"inconclusive={verdicts[Verdict.SKIPPED]}"
    )


@settings(max_examples=6, derandomize=True, database=None, deadline=None)
@seed(20261108)
@given(order=st.permutations(("alpha", "beta", "gamma")))
def test_reordering_rule_preserves_tools_and_is_idempotent(order: tuple[str, ...]) -> None:
    """SL007 is the only registered rule whose behavior needs multiple tools."""
    rule_cls = next(cls for cls in all_rules() if cls.id == "SL007")
    tools = tuple(ToolDef(name=name, input_schema={"type": "object"}) for name in order)
    expected = tuple(sorted(tools, key=lambda tool: tool.name))
    result = rule_cls().apply(tools, resolve_options(rule_cls))

    assert result == expected
    assert all(result[i] is expected[i] for i in range(len(tools)))
    assert rule_cls().apply(result, resolve_options(rule_cls)) is result
