# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Exact `gemini:<model>` token counts via Google's `count_tokens` API.
- `examples/openai_startup.py`: `slim_tools` with the OpenAI SDK.
- README: offline use of the default tokenizer, a security note on `--mcp`, a cost estimate
  in the FAQ, and a Stability section defining the public API and the deprecation policy.
- pre-commit hook `toolism-check` (`.pre-commit-hooks.yaml`), the same budget gate as
  `toolism check`, run before each commit.

### Changed

- `--verify` no longer throws away every change to a tool when one rule breaks it: it finds the
  rules responsible, reverts only those, and keeps the other rules' savings (re-verified).
  `Rollback` gains a `reverted` field, also in the JSON report.

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
