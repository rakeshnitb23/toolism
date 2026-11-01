# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-31

toolism's second release lets you see every change before you make it, verifies more precisely,
slims more schemas, and adds a local gate. Output stays validation-equivalent at the safe level,
and nothing is removed or renamed, so upgrading from 0.1.0 needs no changes.

### Highlights

- **See it before you slim it.** The new `toolism diff` shows a per-tool unified diff, in colour,
  from a file, stdin or a live MCP server, and `--exit-code` turns it into a CI check.
- **Smarter `--verify`.** When a rule breaks a tool, toolism now reverts only that rule and keeps
  the rest of the savings, instead of restoring the whole tool.
- **More schemas slimmed.** The new SL010 rule collapses single-branch `allOf` wrappers, and SL002
  now rewrites `date | None`-style fields. At `--level annotations`, SL103 and SL104 drop titles
  and repeated descriptions.
- **Budgets before the commit.** The new `toolism-check` pre-commit hook runs the same gate as CI,
  and the GitHub Action gains an `mcp-timeout` input.
- **Fewer API calls for exact counts.** An opt-in persistent cache reuses Anthropic token counts
  between runs.
- **More trustworthy results.** Fixes to SL008 and `--verify` remove cases where output could
  differ from what was promised, and Windows tool files with a UTF-8 BOM now load.
- **Python 3.14** is now tested and supported.

Thank you to everyone who contributed, including 19 people who made their first contribution
to toolism in this release.

### Added

- `toolism diff`: per-tool unified previews of changed descriptions and input schemas,
  with the same level/rule selection as `slim`, stdin and output-file support, and
  terminal colour (`-` red, `+` green, `@@` cyan) that respects `NO_COLOR`.
- `toolism diff --mcp COMMAND` previews a live stdio server's tools, with the same
  `--mcp-timeout SECONDS` and input validation as `measure`, `slim` and `check` (#95).
- `toolism diff` gains an `--exit-code` flag that exits 1 when any tool would change,
  making it easy to gate on unslimmed schemas in CI.
- SL010 `single-allof` collapses single-branch `allOf` wrappers when their keywords can be
  merged without changing validation. Strict tools and uncertain dialect or reference scopes
  are left alone.
- `SL103 drop-titles`: opt-in annotation-level rule that removes schema `title` keywords.
- `SL104 dedupe-descriptions`: lossy annotation rule that drops property descriptions matching a
  whole sentence or line of the tool description.
- Opt-in persistent Anthropic token-count caching via `TOOLISM_CACHE_DIR`, including baseline,
  probe, marginal and ordered aggregate counts. Bounded, checksummed snapshots use atomic
  replacement and fixed 24-hour generations; README documents clearing and Actions cache use.
- pre-commit hook `toolism-check` (`.pre-commit-hooks.yaml`), the same budget gate as
  `toolism check`, run before each commit.
- GitHub Action: the optional `mcp-timeout` input sets the stdio MCP server timeout; leaving it
  empty keeps the 60-second default. The README now documents every input and output.
- Python 3.14 is now tested in CI and listed in the package classifiers.
- `examples/openai_startup.py`: `slim_tools` with the OpenAI SDK.
- README: offline use of the default tokenizer, a security note on `--mcp`, a cost estimate
  in the FAQ, a Stability section defining the public API and the deprecation policy, and an
  Environment variables section listing every variable toolism reads.

### Changed

- SL002 now hoists `format`, `pattern`, `minLength` and `maxLength` when rewriting a nullable
  `anyOf`/`oneOf`, so `date | None` (`anyOf: [{type: string, format: date}, {type: null}]`)
  becomes `{type: [string, null], format: date}`. It only does so when exactly one branch is
  `type: string` and no other branch can be a string; any other extra keyword leaves the node
  unchanged.
- `--verify` no longer throws away every change to a tool when one rule breaks it: it finds the
  rules responsible, reverts only those, and keeps the other rules' savings (re-verified).
  `Rollback` gains a `reverted` field, also in the JSON report.
- GitHub release notes now list the release's pull requests and new contributors.

### Fixed

- SL008 no longer drops `additionalProperties: true`, `items: {}` and similar keywords from a tool
  whose schema restricts `unevaluatedProperties` or `unevaluatedItems`. These keywords mark
  locations as evaluated there, so dropping them could reject tool calls that were valid.
- `--verify` no longer rewrites the schemas it checks. hypothesis-jsonschema resolves `$ref`s in
  place, so with `--verify` the slimmed output could come back with `$ref`s expanded (more tokens
  than the report showed), and the caller's input tool set was modified too. Verification now
  works on copies; output is identical with and without `--verify`.
- Tool files saved with a UTF-8 BOM (PowerShell, some Windows editors) now load from a path,
  from stdin, and through `toolism.loads`; the BOM is stripped before parsing.
- A missing input file now comes with a hint to check the path, or to use `-` or `--mcp`.
- `toolism diff` now reports `No changes.` on stderr when the rendered diff is empty.
- `toolism measure` now preserves duplicate tool name warnings in table, Markdown and JSON output.
- The source distribution now includes the examples, benchmark corpus and documentation fixtures,
  so downstream packagers can run its test suite.

## [0.1.0] - 2026-10-23

### Added

- `toolism measure`: token cost of every tool in OpenAI (Chat and Responses), Anthropic or MCP
  tool JSON, or of a live stdio MCP server (`--mcp`), as a table, Markdown or JSON.
- `toolism slim`: validation-equivalent rewriting with nine safe rules (SL001–SL009) and two
  lossy annotation rules (SL101, SL102, `--level annotations`). The output keeps the input's
  format and envelope; per-rule and per-tool savings go to stderr.
- `--verify`: bidirectional equivalence checking of every changed schema. Annotation-only changes
  are proved; other changes are searched for counterexamples with hypothesis-jsonschema, and any
  tool with one is rolled back.
- `toolism check`: CI gate against a committed baseline with growth budgets
  (`--max-increase 10%` and/or tokens), `--max-total` and `--max-tool`. Writes a Markdown summary
  to `$GITHUB_STEP_SUMMARY`.
- `toolism rules`: the rule catalogue as a table, JSON, or the generated `docs/rules.md`.
- Tokenizers: offline `heuristic`, `tiktoken:<encoding>` (default `o200k_base`) and exact
  `anthropic:<model>` via the `count_tokens` API.
- Python API: `load`, `parse`, `measure`, `slim`, `dump`, `slim_tools` (cached, for agent
  code), `list_rules`, `load_mcp_stdio`.
- GitHub Action (`uses: rakeshnitb23/toolism@v0.1.0`) with `status`, `total`, `delta` and
  `summary-file` outputs.
- Benchmark on 14 real tool sets (159 tools): 37.4% fewer tokens in total, median 11.3% per tool
  set, with zero equivalence counterexamples.

[Unreleased]: https://github.com/rakeshnitb23/toolism/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rakeshnitb23/toolism/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rakeshnitb23/toolism/releases/tag/v0.1.0

