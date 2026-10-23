"""The slimming pipeline (design C7).

1. Select rules (level, enable, disable) and validate their options.
2. Apply content rules in id order, repeating passes until nothing changes (max ``max_passes``).
   Every change is attributed to ``(rule, tool)`` with a cheap local tokenizer.
3. Optionally verify every changed tool; a counterexample rolls that tool back to its original.
4. Apply tool-order rules (they change no content).
5. Measure before/after with the reporting tokenizer.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from toolism.errors import MissingExtraError, RuleError, ToolismError
from toolism.models import ToolDef, ToolSet
from toolism.rules import Rule, Safety, all_rules, get_rule, resolve_options
from toolism.tokenizers import HeuristicTokenizer, Tokenizer, get_tokenizer
from toolism.verify import Verdict, VerificationResult, check_equivalence, verify_available

__all__ = [
    "Rollback",
    "RuleStat",
    "SlimReport",
    "SlimResult",
    "ToolStat",
    "select_rules",
    "slim",
]


@dataclass(frozen=True)
class RuleStat:
    rule_id: str
    name: str
    tokens_saved: int
    tools_changed: tuple[str, ...]


@dataclass(frozen=True)
class ToolStat:
    name: str
    before: int
    after: int

    @property
    def saved(self) -> int:
        return self.before - self.after


@dataclass(frozen=True)
class Rollback:
    """Changes undone because verification found a counterexample.

    ``rules`` changed the tool; ``reverted`` are the ones whose changes were undone. The others
    are kept when, applied without the reverted rules, verification finds no counterexample.
    """

    tool: str
    rules: tuple[str, ...]
    direction: str | None
    example: Any
    reverted: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlimReport:
    tokenizer: str
    approximate: bool
    attribution_tokenizer: str
    level: str
    before: int
    after: int
    tool_count: int
    uncounted: tuple[str, ...]
    rules: tuple[RuleStat, ...]
    tools: tuple[ToolStat, ...]
    passes: int
    warnings: tuple[str, ...]
    rollbacks: tuple[Rollback, ...]
    verification: tuple[VerificationResult, ...]

    @property
    def saved(self) -> int:
        return self.before - self.after

    @property
    def saved_pct(self) -> float:
        return 100.0 * self.saved / self.before if self.before else 0.0


@dataclass(frozen=True)
class SlimResult:
    toolset: ToolSet
    report: SlimReport


def select_rules(
    level: Safety | str = Safety.SAFE,
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
) -> list[type[Rule]]:
    """Default-enabled rules up to ``level``, plus ``enable``, minus ``disable``; sorted by id."""
    ceiling = Safety.parse(level)
    chosen = {r.id: r for r in all_rules() if r.default_enabled and r.safety <= ceiling}
    for key in enable:
        rule = get_rule(key)
        chosen[rule.id] = rule
    for key in disable:
        chosen.pop(get_rule(key).id, None)
    return [chosen[rule_id] for rule_id in sorted(chosen)]


def _resolve_options(
    rules: Sequence[type[Rule]], options: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, dict[str, Any]]:
    given: dict[str, Mapping[str, Any]] = {}
    for key, values in (options or {}).items():
        given[get_rule(key).id] = values
    selected = {rule.id for rule in rules}
    for rule_id in given:
        if rule_id not in selected:
            raise RuleError(
                f"options given for {rule_id}, which is not enabled", hint=f"add --enable {rule_id}"
            )
    return {rule.id: resolve_options(rule, given.get(rule.id)) for rule in rules}


def _fresh(tool: ToolDef) -> ToolDef:
    """A copy whose schema a misbehaving rule could mutate without touching ours."""
    if tool.opaque or not isinstance(tool.input_schema, (dict, list)):
        return tool
    return tool.replace(input_schema=copy.deepcopy(tool.input_schema))


def _run_rule(rule_cls: type[Rule], tools: tuple[ToolDef, ...], options: dict[str, Any]) -> Any:
    try:
        return rule_cls().apply(tools, options)
    except ToolismError:
        raise
    except Exception as exc:
        raise RuleError(f"{rule_cls.id} failed: {type(exc).__name__}: {exc}") from exc


def _check_content_contract(
    rule_cls: type[Rule], before: tuple[ToolDef, ...], after: Any
) -> tuple[ToolDef, ...]:
    if not isinstance(after, tuple) or len(after) != len(before):
        raise RuleError(
            f"{rule_cls.id} broke the rule contract: it must return the same number of tools"
        )
    for old, new in zip(before, after, strict=True):
        if not isinstance(new, ToolDef) or new.name != old.name or new.opaque != old.opaque:
            raise RuleError(
                f"{rule_cls.id} broke the rule contract on tool {old.name!r}: "
                "names, order and opaque tools must not change"
            )
        if old.opaque and new is not old:
            raise RuleError(
                f"{rule_cls.id} broke the rule contract: it rewrote opaque tool {old.name!r}"
            )
    return after


def _apply_reorder(
    rule_cls: type[Rule], tools: tuple[ToolDef, ...], options: dict[str, Any]
) -> tuple[ToolDef, ...]:
    after = _run_rule(rule_cls, tools, options)
    by_id = {id(tool) for tool in tools}
    if (
        not isinstance(after, tuple)
        or len(after) != len(tools)
        or {id(tool) for tool in after} != by_id
    ):
        raise RuleError(
            f"{rule_cls.id} broke the rule contract: an ordering rule may only reorder the "
            "same tool objects"
        )
    return after


@dataclass
class _Applied:
    """The outcome of running content rules to a fixpoint."""

    tools: list[ToolDef]
    deltas: dict[tuple[str, int], int]
    touched: dict[int, list[str]]
    passes: int
    converged: bool


def _apply_content(
    original: tuple[ToolDef, ...],
    content_rules: Sequence[type[Rule]],
    resolved: Mapping[str, dict[str, Any]],
    attribution: Tokenizer,
    max_passes: int,
) -> _Applied:
    current = list(original)
    deltas: dict[tuple[str, int], int] = defaultdict(int)
    touched: dict[int, list[str]] = defaultdict(list)
    passes = 0
    for passes in range(1, max_passes + 1):
        changed_in_pass = False
        for rule_cls in content_rules:
            inputs = tuple(_fresh(tool) for tool in current)
            outputs = _check_content_contract(
                rule_cls, inputs, _run_rule(rule_cls, inputs, resolved[rule_cls.id])
            )
            for index, (given, produced) in enumerate(zip(inputs, outputs, strict=True)):
                if produced is given or produced.fingerprint() == current[index].fingerprint():
                    continue
                saved = attribution.count_tool(current[index]) - attribution.count_tool(produced)
                deltas[(rule_cls.id, index)] += saved
                if rule_cls.id not in touched[index]:
                    touched[index].append(rule_cls.id)
                current[index] = produced
                changed_in_pass = True
        if not changed_in_pass:
            return _Applied(current, deltas, touched, passes, converged=True)
    return _Applied(current, deltas, touched, passes, converged=not content_rules)


def _isolate(
    index: int,
    original: tuple[ToolDef, ...],
    content_rules: Sequence[type[Rule]],
    suspects: Sequence[str],
    resolved: Mapping[str, dict[str, Any]],
    attribution: Tokenizer,
    max_passes: int,
    verify_examples: int,
) -> tuple[_Applied | None, tuple[str, ...]]:
    """Find which of ``suspects`` (rules that changed tool ``index``) make it non-equivalent.

    Rules are added back one at a time, in id order; each candidate is the whole pipeline re-run
    with the rules accepted so far plus one, so interactions between rules are verified too. A
    rule whose candidate has a counterexample is reverted. Returns the last accepted run (``None``
    when every suspect is reverted) and the reverted rule ids.
    """
    before = original[index]
    others = [r for r in content_rules if r.id not in suspects]
    accepted: list[type[Rule]] = []
    kept: _Applied | None = None
    reverted: list[str] = []
    for rule_cls in content_rules:
        if rule_cls.id not in suspects:
            continue
        trial = _apply_content(
            original,
            sorted([*others, *accepted, rule_cls], key=lambda r: r.id),
            resolved,
            attribution,
            max_passes,
        )
        after = trial.tools[index]
        if after.fingerprint() != before.fingerprint():
            verdict = check_equivalence(
                before.input_schema,
                after.input_schema,
                tool=before.name,
                max_examples=verify_examples,
            ).verdict
            if verdict is Verdict.COUNTEREXAMPLE:
                reverted.append(rule_cls.id)
                continue
        accepted.append(rule_cls)
        kept = trial
    return kept, tuple(reverted)


def slim(
    toolset: ToolSet,
    *,
    level: Safety | str = Safety.SAFE,
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
    options: Mapping[str, Mapping[str, Any]] | None = None,
    rules: Sequence[type[Rule]] | None = None,
    tokenizer: str | Tokenizer | None = None,
    verify: bool = False,
    verify_examples: int = 50,
    max_passes: int = 3,
) -> SlimResult:
    """Slim every tool in ``toolset`` and report what was saved.

    ``rules`` overrides selection by ``level``/``enable``/``disable`` with an explicit list.
    With ``verify=True`` every changed schema is checked for equivalence and rolled back on a
    counterexample (needs the ``verify`` extra).
    """
    if max_passes < 1:
        raise RuleError("max_passes must be at least 1")
    if verify and not verify_available():
        raise MissingExtraError("Equivalence verification", "verify")
    ceiling = Safety.parse(level)
    chosen = list(rules) if rules is not None else select_rules(ceiling, enable, disable)
    resolved = _resolve_options(chosen, options)
    reporting = get_tokenizer(tokenizer)
    attribution: Tokenizer = reporting if reporting.local else HeuristicTokenizer()

    content_rules = [r for r in chosen if not r.reorders]
    order_rules = [r for r in chosen if r.reorders]
    original = toolset.tools
    applied = _apply_content(original, content_rules, resolved, attribution, max_passes)
    current, deltas, touched, passes = (
        applied.tools,
        applied.deltas,
        applied.touched,
        applied.passes,
    )
    warnings = list(toolset.warnings)
    if not applied.converged and max_passes > 1:
        warnings.append(
            f"rules were still changing tools after {max_passes} passes; "
            "the result may not be minimal"
        )

    results: list[VerificationResult] = []
    rollbacks: list[Rollback] = []
    if verify:
        for index, (before, after) in enumerate(zip(original, current, strict=True)):
            if before.opaque or after.fingerprint() == before.fingerprint():
                continue
            result = check_equivalence(
                before.input_schema,
                after.input_schema,
                tool=before.name,
                max_examples=verify_examples,
            )
            results.append(result)
            if result.verdict is not Verdict.COUNTEREXAMPLE:
                continue
            suspects = tuple(touched[index])
            kept, reverted = _isolate(
                index,
                original,
                content_rules,
                suspects,
                resolved,
                attribution,
                max_passes,
                verify_examples,
            )
            if not reverted:  # isolation found no single culprit: undo every change
                kept, reverted = None, suspects
            rollbacks.append(
                Rollback(before.name, suspects, result.direction, result.example, reverted)
            )
            for rule_id in touched.pop(index):
                deltas.pop((rule_id, index), None)
            if kept is None:
                current[index] = before
                warnings.append(
                    f"rolled back {before.name!r}: rules {', '.join(suspects)} "
                    f"produced a schema that is not equivalent ({result.direction})"
                )
                continue
            current[index] = kept.tools[index]
            if kept.touched.get(index):
                touched[index] = kept.touched[index]
            for (rule_id, i), saved in kept.deltas.items():
                if i == index:
                    deltas[(rule_id, index)] = saved
            warnings.append(
                f"reverted rules {', '.join(reverted)} on {before.name!r}: they produced a "
                f"schema that is not equivalent ({result.direction}); kept the other changes"
            )

    final = tuple(current)
    for rule_cls in order_rules:
        final = _apply_reorder(rule_cls, final, resolved[rule_cls.id])

    rule_stats = tuple(
        RuleStat(
            rule_id=rule_cls.id,
            name=rule_cls.name,
            tokens_saved=sum(v for (rid, _), v in deltas.items() if rid == rule_cls.id),
            tools_changed=tuple(
                original[i].name for i in sorted(touched) if rule_cls.id in touched[i]
            ),
        )
        for rule_cls in chosen
    )
    tool_stats = tuple(
        ToolStat(before.name, reporting.count_tool(before), reporting.count_tool(after))
        for before, after in zip(original, current, strict=True)
        if not before.opaque
    )
    function_before = [t for t in original if not t.opaque]
    function_after = [t for t in final if not t.opaque]
    report = SlimReport(
        tokenizer=reporting.name,
        approximate=reporting.approximate,
        attribution_tokenizer=attribution.name,
        level=ceiling.label,
        before=reporting.count_tools(function_before),
        after=reporting.count_tools(function_after),
        tool_count=len(function_before),
        uncounted=tuple(t.name for t in original if t.opaque),
        rules=rule_stats,
        tools=tool_stats,
        passes=passes,
        warnings=tuple(warnings),
        rollbacks=tuple(rollbacks),
        verification=tuple(results),
    )
    return SlimResult(toolset.replace_tools(final), report)
