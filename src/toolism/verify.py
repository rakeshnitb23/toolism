"""Bidirectional, property-based equivalence checking of two JSON Schemas (design C8).

For each direction (original→slim, slim→original) Hypothesis generates instances that the source
schema accepts (via ``hypothesis-jsonschema``) and checks that the destination accepts them too.
Passing both directions is strong *evidence* — not proof — that the schemas accept exactly the
same tool calls. Schemas the generator cannot handle are reported as SKIPPED, never as passed.

Needs the ``verify`` extra: ``pip install 'toolism[verify]'``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from toolism.errors import MissingExtraError
from toolism.schema import SchemaPath, detect_dialect, map_subschemas, ref_siblings_apply

__all__ = ["Verdict", "VerificationResult", "check_equivalence", "verify_available"]

ORIGINAL_TO_SLIM = "original→slim"
SLIM_TO_ORIGINAL = "slim→original"


class Verdict(str, Enum):
    EQUIVALENT = "equivalent"
    COUNTEREXAMPLE = "counterexample"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of comparing one tool's original and slimmed input schema."""

    tool: str
    verdict: Verdict
    direction: str | None = None
    """For COUNTEREXAMPLE: the direction that failed."""
    example: Any = None
    """For COUNTEREXAMPLE: a (shrunk) instance accepted by one schema and rejected by the other."""
    reason: str = ""
    """For SKIPPED: why instances could not be generated or validated."""
    method: str = ""
    """How the verdict was reached: ``identical``, ``annotations-only`` (a proof: the schemas
    differ only in keywords that never affect validation) or ``sampled`` (property-based)."""


class _Counterexample(Exception):
    def __init__(self, instance: Any) -> None:
        super().__init__("counterexample")
        self.instance = instance


def verify_available() -> bool:
    """Whether the optional verification dependencies are installed."""
    try:
        import hypothesis  # noqa: F401
        import hypothesis_jsonschema  # noqa: F401
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


# Keywords that never affect validation (JSON Schema "annotation" keywords).
ANNOTATION_KEYWORDS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "example",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)


def _canonical(schema: Any) -> str:
    return json.dumps(schema, sort_keys=True, separators=(",", ":"))


def _without_annotations(schema: Any) -> Any:
    """``schema`` minus annotation keywords in every subschema (validation is unchanged)."""

    def strip(node: dict[str, Any], path: SchemaPath) -> Any:
        return {k: v for k, v in node.items() if k not in ANNOTATION_KEYWORDS}

    return map_subschemas(schema, strip)


def _validator(schema: Any) -> Any:
    import jsonschema
    from jsonschema.validators import validator_for

    if isinstance(schema, bool):
        return jsonschema.Draft202012Validator(schema)
    cls = validator_for(schema, default=jsonschema.Draft202012Validator)
    return cls(schema)


_DEF_CONTAINERS = ("$defs", "definitions")


def _generation_schema(schema: Any) -> Any:
    """Rewrite ``schema`` so the generator follows the *validator's* dialect semantics.

    hypothesis-jsonschema always applies keywords next to ``$ref``. Under draft <= 7 validators
    ignore them, so we drop them before generating; otherwise the generator would never produce
    the instances that tell the two semantics apart (found by tests/test_verify.py). Definition
    containers are kept so JSON-pointer refs still resolve. ``$schema`` is removed because the
    generator does not need it.
    """
    if not isinstance(schema, dict):
        return schema
    if not ref_siblings_apply(detect_dialect(schema)):

        def ignore_ref_siblings(node: dict[str, Any], path: SchemaPath) -> Any:
            if "$ref" not in node:
                return node
            kept = {"$ref": node["$ref"]}
            kept.update({k: v for k, v in node.items() if k in _DEF_CONTAINERS})
            return kept

        schema = map_subschemas(schema, ignore_ref_siblings)
    if "$schema" in schema:
        schema = {k: v for k, v in schema.items() if k != "$schema"}
    return schema


def _search(source: Any, accepts_source: Any, accepts_dest: Any, max_examples: int) -> Any:
    """Return ``None`` (no counterexample), the counterexample instance, or a ``str`` skip reason.

    The counterexample is wrapped in a 1-tuple so a legitimate ``None`` instance is distinguishable.
    """
    from hypothesis import HealthCheck, Phase, Verbosity, given, settings
    from hypothesis_jsonschema import from_schema

    try:
        strategy = from_schema(_generation_schema(source))
    except Exception as exc:
        return f"cannot generate instances: {type(exc).__name__}: {exc}"

    @settings(
        max_examples=max_examples,
        derandomize=True,
        database=None,
        deadline=None,
        suppress_health_check=list(HealthCheck),
        verbosity=Verbosity.quiet,
        phases=(Phase.generate, Phase.shrink),
    )
    @given(strategy)
    def probe(instance: Any) -> None:
        if not accepts_source.is_valid(instance):
            return  # generator imprecision: not a real instance of the source schema
        if not accepts_dest.is_valid(instance):
            raise _Counterexample(instance)

    try:
        probe()
    except _Counterexample as found:
        return (found.instance,)
    except Exception as exc:
        return f"cannot generate instances: {type(exc).__name__}: {exc}"
    return None


def check_equivalence(
    original: Any, slim: Any, *, tool: str = "", max_examples: int = 50
) -> VerificationResult:
    """Check that ``original`` and ``slim`` accept the same instances, in both directions.

    ``None`` schemas mean "absent" and are treated as ``{}`` (accept anything).
    Raises :class:`MissingExtraError` if the ``verify`` extra is not installed.
    """
    left = {} if original is None else original
    right = {} if slim is None else slim
    if _canonical(left) == _canonical(right):
        return VerificationResult(tool, Verdict.EQUIVALENT, method="identical")
    if _canonical(_without_annotations(left)) == _canonical(_without_annotations(right)):
        return VerificationResult(tool, Verdict.EQUIVALENT, method="annotations-only")
    if not verify_available():
        raise MissingExtraError("Equivalence verification", "verify")
    # hypothesis-jsonschema resolves $refs in place, so never hand it the caller's schemas:
    # it would rewrite the slimmed output (and the user's input) behind our back.
    left, right = copy.deepcopy(left), copy.deepcopy(right)

    try:
        accepts_left, accepts_right = _validator(left), _validator(right)
    except Exception as exc:
        return VerificationResult(
            tool, Verdict.SKIPPED, reason=f"invalid schema: {exc}", method="sampled"
        )

    skipped: list[str] = []
    for source, accepts_source, accepts_dest, direction in (
        (left, accepts_left, accepts_right, ORIGINAL_TO_SLIM),
        (right, accepts_right, accepts_left, SLIM_TO_ORIGINAL),
    ):
        outcome = _search(source, accepts_source, accepts_dest, max_examples)
        if isinstance(outcome, tuple):
            return VerificationResult(
                tool,
                Verdict.COUNTEREXAMPLE,
                direction=direction,
                example=outcome[0],
                method="sampled",
            )
        if isinstance(outcome, str):
            skipped.append(f"{direction}: {outcome}")
    if skipped:
        return VerificationResult(
            tool, Verdict.SKIPPED, reason="; ".join(skipped), method="sampled"
        )
    return VerificationResult(tool, Verdict.EQUIVALENT, method="sampled")
