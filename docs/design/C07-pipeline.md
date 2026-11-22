# C7 — Pipeline

## Responsibility

Select rules, apply them to a fixpoint, attribute savings, verify and roll back, and produce the final report.

## Interface

```python
@dataclass(frozen=True) class RuleStat: rule_id: str; name: str; tokens_saved: int; tools_changed: tuple[str, ...]
@dataclass(frozen=True) class ToolStat: name: str; before: int; after: int; rules: tuple[str, ...] = ()
@dataclass(frozen=True) class Rollback: tool: str; rules: tuple[str, ...]; direction: str; example: Any
@dataclass(frozen=True) class SlimReport:
    tokenizer: str; approximate: bool; attribution_tokenizer: str
    before: int; after: int; tool_count: int; uncounted: tuple[str, ...]
    rules: tuple[RuleStat, ...]; tools: tuple[ToolStat, ...]
    passes: int; warnings: tuple[str, ...]; rollbacks: tuple[Rollback, ...]
    verification: tuple[VerificationResult, ...]   # empty unless verify=True
    @property saved -> int; @property saved_pct -> float
@dataclass(frozen=True) class SlimResult: toolset: ToolSet; report: SlimReport; before_reorder: ToolSet | None = None

def select_rules(level: Safety | str = "safe", enable=(), disable=()) -> list[Rule]
def slim(toolset, *, level="safe", enable=(), disable=(), options=None, tokenizer=None,
         verify=False, verify_examples=50, max_passes=3) -> SlimResult
```

## Behaviour

**1. Rule selection.**
- Start from rules with `default_enabled` and `safety <= level`.
- Add `enable` (any safety — explicit opt-in).
- Remove `disable`.
- Unknown id/name → `RuleError`.
- Order by id.

**2. Options.** `{rule_id: {key: value}}` is resolved through the registry, validated before any work starts.

**3. Context per tool.** `RuleContext(tool, dialect=detect_dialect(schema), strict=tool.strict, options)`.

**4. Passes** (up to `max_passes`). For each rule:
1. `new = rule.apply(current, opts)`.
2. Check the rule contract.
3. For each index where the fingerprint changed: `delta = attr.count_tool(old) − attr.count_tool(new)`; accumulate into the rule's stat and record the tool and rule.
4. If no rule changed anything in a pass → stop.
5. If the last pass still changed something → warning `"did not reach a fixpoint after N passes"`.

**5. Attribution tokenizer.** The reporting tokenizer if it's local, otherwise `HeuristicTokenizer`.

**6. Verify** (optional). For each tool whose schema fingerprint changed: `check_equivalence(original.input_schema, new.input_schema, dialect, max_examples)`.
- **Counterexample** → restore the original tool; record a `Rollback` with the rules that touched it. Its attribution is removed from the rule stats.
- **Skipped** → kept, and listed in `verification`.

**7. Final counts.** Reporting tokenizer: `before` / `after` totals via `count_tools`; per-tool via `count_tool`.

**8. Return** the slimmed `ToolSet` (same document and path) and the report.

## Edge cases

- Empty tool set → a zero report.
- All opaque → nothing changes; `uncounted` lists them.
- A rule that raises → wrapped in `RuleError("SL00x failed on tool 'name': …")`. No partial output.
- Inputs are never mutated: every rule receives `copy.deepcopy`'d tools.

## Tests

- Rule selection by level, enable and disable; unknown rule.
- Fixpoint: a scenario where SL004 inlining exposes a title only SL001 removes (needs 2 passes); pass cap warning with a synthetic flip-flop rule.
- Attribution sums: `sum(rule.saved)` equals `attr(before) − attr(after)` when the attribution tokenizer is the reporting one.
- Rollback: a synthetic broken rule (drops `required`) plus `verify=True` → tool restored, rollback recorded, stats adjusted.
- Contract violations raise.
- Inputs unchanged (fingerprints).
- Performance smoke test: 1,000 generated tools under 2 s with the heuristic tokenizer.

## Review

| Issue | Change |
|---|---|
| Attribution with a remote tokenizer would cost rules × passes × N API calls. | Attribution tokenizer split out (review). |
| After a rollback the per-rule savings were wrong, because they still included the rolled-back tool. | The pipeline keeps per-(rule, tool) deltas and subtracts them on rollback. |
| Should `verify` roll back individual rules instead of the whole tool? | Whole tool for v0.1 (review R8). Per-rule bisection is a listed good-first-issue. |
| Deep-copying every tool for every rule is wasteful for big sets. | Acceptable at measured scale (benchmark target). Copy-on-write is a later optimization if profiling demands it. |
