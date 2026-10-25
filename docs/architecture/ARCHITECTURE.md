# toolism — Architecture

Status: **v0.1 plan — reviewed** · Review: [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) (applied in section 13)

## 1. Problem

An LLM agent sends the full definition of every tool (name, description, JSON Schema of its arguments) as input on **every** model call. Large MCP servers spend 10k–100k+ tokens on definitions before the agent reads the user's request.

Much of that is mechanical bloat produced by schema generators (Pydantic, FastMCP, zod-to-json-schema):

- a `title` on every property that only repeats the property name
- `anyOf: [{type: X}, {type: null}]` + `default: null` for every optional field
- `$defs` that are used once, or never
- descriptions that restate the parameter name
- docstring indentation and blank lines
- keywords that are no-ops (`required: []`, `additionalProperties: true`)

Teams measure this with hand-written scripts. Nobody ships a reusable tool that **removes** the bloat **and proves the result still accepts exactly the same tool calls**.

## 2. Goals and non-goals

### Goals (v0.1)

1. **Measure** — token cost per tool and in total, for OpenAI (Chat + Responses), Anthropic and MCP tool definitions.
2. **Slim** — rewrite definitions with small, composable, deterministic **rules**. The default rule set is *safe*: validation-equivalent, and it drops only information that is provably redundant.
3. **Prove** — optional property-based **equivalence verification** of the original vs slimmed schema, in both directions.
4. **Gate** — a CI check comparing tool-token cost against a committed baseline, with budgets. Ships as a CLI and a GitHub Action.
5. **Embed** — a small Python API (`measure`, `slim`) that agent code can call at startup. Output keeps the same format as the input, so it's a drop-in.
6. **Contributable** — one rule = one module + fixtures, discovered automatically.

### Non-goals

- Compressing tool **outputs**, conversation history or prompts (Headroom, LLMLingua, sqz already do this).
- Selecting *which* tools to send (tool search / routing).
- Any LLM call in the slimming path. Slimming is pure, deterministic and offline.
- Being a proxy or gateway.

## 3. Key terms

| Term | Meaning |
|---|---|
| **ToolDef** | Format-neutral tool: `name`, `description`, `input_schema`, plus `extras` (unknown keys, preserved) |
| **ToolSet** | Ordered `ToolDef`s + the source format + the original envelope, so we can write back exactly what we read |
| **Rule** | A pure transformation `ToolSet → ToolSet` with an ID (`SL001`), a safety level and options |
| **Safety level** | `SAFE` (validation-equivalent; only redundant information removed), `LOSSY_ANNOTATIONS` (validation-equivalent but drops non-redundant text such as long descriptions or examples), `CHANGES_VALIDATION` (may change accepted inputs; reserved, no v0.1 rules) |
| **Equivalence** | For schemas A and B under the declared JSON Schema dialect: every instance valid under A is valid under B, and vice versa |
| **Tokenizer** | Counts tokens of a rendered tool. Local (heuristic, tiktoken) or remote (Anthropic `count_tokens`). |

## 4. Component map

```
                                ┌───────────────────────── toolism (library) ─────────────────────────┐
                                │                                                                      │
 tools.json / stdin ──► io ──►  │  formats (detect · parse · serialize)                                │
 MCP server (stdio) ──► loaders │        │                                                             │
                                │        ▼                                                             │
                                │     models: ToolDef / ToolSet  ◄──────── errors                      │
                                │        │                                                             │
                                │        ├──────────► measure ──► tokenizers ──► report (table/json/md)│
                                │        │                          ▲                                  │
                                │        ▼                          │                                  │
                                │     pipeline (slim) ──► rules ──► schema utils (walker, refs, dialect)│
                                │        │       (fixpoint, attribution)                               │
                                │        ▼                                                             │
                                │     verify (hypothesis-jsonschema + jsonschema)  [extra: verify]     │
                                │        │                                                             │
                                │     check (baseline + budgets)                                       │
                                └────────┼─────────────────────────────────────────────────────────────┘
                                         ▼
                              cli (typer: measure · slim · check · rules)  ──►  action.yml (GitHub Action)
```

### Dependency rule

Layers may only import downward. CI enforces this with an import-linter-style test.

```
cli, action            (top: user-facing)
check, measure, pipeline, verify
rules                  (depends on schema utils, models, tokenizers only via ctx)
schema utils, tokenizers, formats, loaders
models, errors         (bottom: no internal deps)
```

- `rules` never imports `pipeline` or `cli`.
- `models` imports nothing internal.

## 5. Components

| # | Component | Module(s) | Responsibility |
|---|---|---|---|
| C0 | Scaffold | `pyproject.toml`, CI | Packaging (hatchling, src layout), ruff, mypy strict, pytest, coverage |
| C1 | Models & errors | `models.py`, `errors.py` | Immutable `ToolDef`, `ToolSet`, `ToolFormat`; exception hierarchy |
| C2 | Formats & IO | `formats.py`, `io.py` | Detect OpenAI-chat / OpenAI-responses / Anthropic / MCP; parse to `ToolSet`; serialize back losslessly |
| C3 | Schema utils | `schema/walker.py`, `schema/refs.py`, `schema/dialect.py` | Keyword-aware traversal of subschemas only; `$ref` / `$defs` analysis; dialect detection |
| C4 | Tokenizers | `tokenizers/*` | `Tokenizer` protocol; `heuristic` (no deps), `tiktoken:<enc>`, `anthropic:<model>`; `get_tokenizer(spec)` |
| C5 | Rule engine | `rules/base.py`, `rules/registry.py` | `Rule`, `ToolRule`, `SchemaRule`, `Safety`, `RuleContext`, `@register_rule`, options |
| C6 | Rules | `rules/sl0xx_*.py`, `rules/sl1xx_*.py` | The rule catalogue (section 6) |
| C7 | Pipeline | `pipeline.py` | Select rules, apply to fixpoint, attribute savings per rule and tool, verify-and-rollback |
| C8 | Verify | `verify.py` | Bidirectional property-based equivalence; deterministic; reports `equivalent / counterexample / skipped` |
| C9 | Measure & report | `measure.py`, `report.py` | `TokenReport`, `SlimReport`; renderers: rich table, JSON (versioned), Markdown |
| C10 | Check | `check.py` | Baseline file, budgets, comparison → `CheckResult`, exit codes |
| C11 | CLI | `cli.py` | `toolism measure / slim / diff / check / rules` |
| C12 | MCP loader | `loaders/mcp.py` | Launch an MCP server over stdio and page through `tools/list` (extra: `mcp`) |
| C13 | GitHub Action & CI | `action.yml`, `.github/workflows/*` | Composite Action wrapping `toolism check`; CI matrix; release via PyPI trusted publishing |
| C14 | Benchmark | `bench/` | Snapshots of public MCP servers + Pydantic-style schemas; results across tokenizers |
| C15 | Docs | `README.md`, `CONTRIBUTING.md`, `docs/` | Quickstart, rule catalogue, contributor guide |

## 6. Rule catalogue (v0.1)

| ID | Name | Safety | Default | What it does |
|---|---|---|---|---|
| SL001 | `redundant-title` | SAFE | on | Drop `title` equal (normalized) to the property name, `$defs` key, or tool name (± `Arguments/Args/Input/Params/Request`) |
| SL002 | `nullable-anyof` | SAFE | on | `anyOf/oneOf` of bare `{type}` branches → `type: [..]` (oneOf only when types are disjoint) |
| SL003 | `null-default` | SAFE | on | Drop `default: null` on properties that aren't `required` |
| SL004 | `inline-defs` | SAFE | on | Inline `$defs` referenced exactly once (non-recursive, no key collision); delete unreferenced `$defs` |
| SL005 | `description-whitespace` | SAFE | on | Dedent docstrings, strip trailing spaces, collapse 3+ newlines and space runs |
| SL006 | `echo-description` | SAFE | on | Drop a property description whose content words equal the property name's words |
| SL007 | `stable-order` | SAFE | **off** | Sort tools by name (0 tokens saved; keeps prompt-cache prefixes stable) |
| SL008 | `noop-keywords` | SAFE | on | Drop `required: []`, empty `description`, empty `$defs`, `additionalProperties: true`, `items: {}`; `type: ["x"]` → `"x"` |
| SL009 | `schema-uri` | SAFE | on | Drop the root `$schema` when the schema has no dialect-sensitive constructs (added in the C6 design review) |
| SL101 | `max-description-length` | LOSSY_ANNOTATIONS | off | Truncate descriptions at a sentence boundary (option `max_chars`, default 300) |
| SL102 | `drop-examples` | LOSSY_ANNOTATIONS | off | Remove `examples` / `example` keywords |

**Levels:**
- `--level safe` (default) runs every *default-on* SAFE rule.
- `--level annotations` also runs LOSSY_ANNOTATIONS rules.
- `--enable` / `--disable` override individual rules.

## 7. Core data flow — `slim`

1. **Load.** `io.load(source)` → `formats.parse(obj)` → `ToolSet` (format detected per item; mixed formats rejected).
2. **Select.** `pipeline.select_rules(level, enable, disable)` → ordered rules (sorted by ID).
3. **Run.** For each pass (max 3, until nothing changes), for each rule:
   - Apply to a deep copy.
   - For every tool the rule changed, recount tokens with the **attribution tokenizer**.
   - Credit the difference to `(rule, tool)`.
4. **Verify (optional).** For each changed tool, `verify.equivalent(original, slim)`. On a counterexample, roll that tool back to the original and record a warning. A rule bug can never ship a broken schema.
5. **Measure.** Final before/after totals with the **reporting tokenizer** (may be remote).
6. **Return.** `SlimResult` (slimmed `ToolSet`, `SlimReport`). `formats.serialize` writes the original envelope and format back.

**Tokenizer roles:**
- The *attribution* tokenizer must be local and cheap; the default is the reporting tokenizer when it's local, otherwise `heuristic`.
- The *reporting* tokenizer is whatever the user picked.

This keeps `anthropic:*` to 2×N API calls instead of rules × passes × N.

## 8. Rendering for token counts

Tokens are counted on a provider-neutral rendering of each tool: compact JSON of `{"name", "description", "parameters"}`, keeping key order, `ensure_ascii=False`.

- This is an **approximation** of what each provider feeds the model.
- `anthropic:<model>` uses Anthropic's own `count_tokens` and is the ground truth for Claude.
- Reports always name the tokenizer and say whether it's approximate.
- MCP `outputSchema`, `annotations` and `title` are preserved in output but not counted, because clients generally don't send them to the model.

## 9. Public API (stable surface for v0.1)

```python
import toolism

ts = toolism.load("tools.json")                    # or toolism.parse(list_of_dicts)
report = toolism.measure(ts, tokenizer="tiktoken:o200k_base")
result = toolism.slim(ts, level="safe", verify=False, tokenizer="heuristic")
result.toolset          # slimmed ToolSet
result.report           # SlimReport: before/after, per-rule, per-tool
toolism.dump(result.toolset)                       # same format/envelope as the input
toolism.dumps(result.toolset)                      # ... or as a string
toolism.loads(text)                                # parse a JSON string
toolism.serialize(result.toolset)                  # back to a JSON-ready document in the input's format
toolism.slim_tools(list_of_dicts) -> list[dict]    # one-liner for agent code
toolism.load_mcp_stdio("npx -y @scope/server")     # tools from a live stdio MCP server (extra: mcp)
toolism.list_rules()
```

The models (`ToolDef`, `ToolSet`, `ToolFormat`, `TokenReport`, `SlimReport`, `SlimResult`,
`Safety`, `Rule`) and the errors (`ToolismError` and its subclasses) are public too. The full list
is `toolism.__all__`; everything else is internal (`toolism._...` or not re-exported).

## 10. CLI

```
toolism measure SOURCE [--tokenizer T] [--format table|json|markdown] [--output FILE]
toolism slim    SOURCE [--output FILE] [--level safe|annotations] [--enable ID]... [--disable ID]...
                        [--option ID.key=value]... [--verify [--verify-examples N] [--fail-on-rollback]]
                        [--tokenizer T] [--report table|json|markdown] [--report-file FILE]
                        [--quiet] [--compact]
toolism check   SOURCE --baseline FILE [--max-increase 10%|500] [--max-total N] [--max-tool N]
                        [--update-baseline] [--allow-tokenizer-change] [--tokenizer T]
                        [--format table|json|markdown] [--output FILE] [--summary-md FILE]
toolism rules   [RULE] [--format table|json|markdown] [--output FILE]

toolism diff    SOURCE [--level safe|annotations] [--enable ID]... [--disable ID]...
                        [--option ID.key=value]... [--output FILE]
```

`measure`, `slim` and `check` also take `--mcp COMMAND` and `--mcp-timeout SECONDS` in place of
`SOURCE`. `toolism --help` and `toolism <command> --help` are the authoritative reference.

- `SOURCE` is a JSON file, `-` for stdin, or `--mcp "<command>"` for a live stdio server.
- `rules RULE` accepts a rule ID or name and shows its full description, safety and options.
  Omitting `RULE` keeps the summary listing; JSON and Markdown retain their existing format.
- **Exit codes:**
  - `0` = OK
  - `1` = budget exceeded (check), or a verification rollback happened (slim `--fail-on-rollback`)
  - `2` = usage or input error
  - `3` = tokenizer unavailable

## 11. Packaging

- Python ≥ 3.10. `src/` layout. hatchling. `py.typed`.
- Core dependencies: `typer` (brings `click`, `rich`), `tiktoken`. No Pydantic in core: models are frozen dataclasses, to keep the library light for embedding.
- Extras:
  - `verify` → `hypothesis`, `hypothesis-jsonschema`, `jsonschema`
  - `mcp` → `mcp`
  - `anthropic` → `anthropic`
  - `all`
- Default tokenizer: `tiktoken:o200k_base`. If its vocabulary can't be loaded (offline), exit 3 with a hint to use `--tokenizer heuristic` or pre-seed `TIKTOKEN_CACHE_DIR`.

## 12. Quality bar

- ruff (lint + format), mypy `--strict` on `src/`, pytest with ≥ 90% line coverage on `src/`.
- Every rule ships before/after fixtures **and** is run through bidirectional equivalence on every benchmark snapshot in CI.
- No network in unit tests. Tests that need tiktoken vocab or real MCP servers are marked and skipped when unavailable.
- Inputs are never mutated (asserted in tests by hashing inputs before and after).

## 13. Decisions applied from the architecture review

See [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) for the reasoning behind each.

- **Walker** — keyword-table driven. Never enters instance-valued (`enum`, `const`, `default`, `examples`) or unknown keywords. (R1, R13)
- **Dialect** — `$schema` decides `$ref`-sibling semantics. SL004 is conservative under draft ≤ 7 and skips tools using `$id`, `$anchor`, `$dynamicRef` or `$recursiveRef`. The verifier uses the same dialect. (R2)
- **Strict mode** — `RuleContext.strict` for OpenAI `strict: true` tools. Rules touching `required` or `additionalProperties` don't apply to them. (R3)
- **Fixpoint** — passes repeat until nothing changes, max 3, with a warning if the cap is hit. (R4)
- **Tokenizer honesty** — tokenizer name and approximate flag on every report and baseline. Default overridable with `TOOLISM_TOKENIZER`. No silent fallback; exit 3. `check` rejects a tokenizer mismatch unless `--allow-tokenizer-change`. (R5, R6)
- **Verification** — runs only on changed tools; 50 examples by default; deterministic; `skipped` is reported, never treated as a pass. (R7)
- **Rollback** — on a counterexample, the rules that changed the tool are added back one at a time (in id order) and each result is re-verified; rules whose result has a counterexample are reverted and the rest are kept. If no single rule is found, the whole tool is restored. `slim --fail-on-rollback` exits 1. This replaces the `--strict-verify` flag from section 10. (R8)
- **Inputs are never mutated** — asserted with `ToolDef.fingerprint()`. (R9)
- **Round-trip** — `serialize(parse(x)) == x` for all fixtures and snapshots. (R10)
- **Layering** — enforced by an AST import test. (R11)
- **Performance** — re-count only changed tools; counts cached by fingerprint. (R12)
- **Main evidence** — CI runs bidirectional equivalence for all default rules on every benchmark snapshot. (R15)

## 14. Security and privacy

- toolism makes **no network calls** of its own, except when the user picks `anthropic:*` (sends definitions to Anthropic) or `--mcp` (runs a local command they supplied, which may itself use the network, e.g. `npx`).
- The default `tiktoken:*` tokenizers download their vocabulary file once, on first use, and cache it (`TIKTOKEN_CACHE_DIR`). Tool definitions never leave the machine. Fully offline: `-t heuristic`.
- `bench/servers.toml` runs commands. It's a developer tool, never read by the library or CLI.

## 15. Implementation notes

The build followed the component designs. Where the implementation deviates, these notes say why. The design docs are kept as they were reviewed.

| Component | Deviation | Why |
|---|---|---|
| Build order | C8 (verify) before C6 (rules); C12 (MCP loader) before C11 (CLI). | Rule fixtures are proved equivalent as they're written, and the CLI's `--mcp` needs the loader. |
| C5 | Rules declare typed `option_specs`; CLI strings are coerced and validated. | `--option SL101.max_chars=abc` must fail with a clear message, not deep inside a rule. |
| C6 | Lossy rules (SL101, SL102) are enabled by `--level annotations` instead of being default-off. | With default-off, `--level annotations` would have added nothing. |
| C7 | Reordering rules (SL007) run after verification and rollback, on the final tools. | Verification compares tools pairwise; reordering first would pair the wrong tools. |
| C8 | Results carry `method` (`identical` / `annotations-only` / `sampled`); annotation-only changes are proved, not sampled. Generation follows the validator's dialect (draft ≤ 7 drops `$ref` siblings). | Faster and stronger. The dialect fix closed a real blind spot found by tests. |
| C9 | Uses `rich` for terminal tables. The module is `toolism.measurement`; the public function stays `toolism.measure`. | A submodule named `measure` would shadow the lazily exported function as soon as it was imported. |
| C10 | With both `--max-increase N%` and `N`, the larger allowance wins (the absolute value is a floor). A baseline with no allowance means "no growth". Duplicate tool names are summed with a note. | Matches the review's intent for small tool sets; a check that always passes would be useless. |
| C11 | Usage errors go through the same `error:`/`hint:` handler (exit 2) instead of click's. Added `--quiet`, `--mcp-timeout`, `-l`. stdout/stderr are switched to UTF-8. `rules --format markdown` generates `docs/rules.md`, replacing `scripts/gen_rules_doc.py`. | Typer may vendor click, so click's exception classes aren't reliable. Windows pipes default to cp1252. |
| C12 | Supports `mcp` SDK 1.x and 2.x by feature detection. Server stderr is captured and its tail becomes the error hint. Adds an async variant. Strips quotes from Windows-style command splits. | SDK 2.x renamed APIs; a failing server's own message is the most useful hint. |
| C13 | Action logic lives in `toolism._action` (stdlib only, tested) and inputs arrive only via `INPUT_*` env vars. Added a `summary-file` output. | No shell interpolation of user input; testable; enables the sticky-comment snippet. |
| C14 | 13 pinned servers plus one generated Pydantic server. The heuristic was not recalibrated: its error vs o200k on the corpus is 3.2%. The equivalence sweep is marked `slow` and runs as its own CI job. | Accuracy was already good; the sweep takes ~3 minutes. |
| C15 | README numbers are asserted by `tests/test_examples.py` against `bench/results.json` and live runs. | Docs can't drift from measured numbers. |
