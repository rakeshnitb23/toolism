# Contributing to toolism

Thanks for helping make tool definitions cheaper. New rules, tokenizers, input formats and
benchmark servers are all welcome, as are bug reports with a tool definition that toolism gets
wrong.

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By taking
part, you agree to uphold it.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
git clone https://github.com/rakeshnitb23/toolism && cd toolism
uv sync --all-extras          # creates .venv with toolism, all extras and dev tools
uv run toolism --version
```

## Checks

CI runs exactly these. Run them before you open a pull request:

```bash
uv run ruff format . && uv run ruff check .    # format and lint
uv run mypy                                    # strict typing on src/
uv run pytest -m "not slow"                    # fast suite (~1 min)
uv run pytest -m slow                          # equivalence on the real-server corpus (~3 min)
uv run pytest --cov                            # coverage must stay >= 90%
```

The tiktoken tests need the `o200k_base` vocabulary. It is downloaded on first use; offline, those
tests are skipped. Set `TIKTOKEN_CACHE_DIR` to reuse a downloaded copy.

The layering test (`tests/test_layering.py`) enforces the architecture: lower layers never import
higher ones, and rules never import the pipeline. If it fails, the import is in the wrong place.
See [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

## Adding a rule in 3 steps

**1. Write the rule** in `src/toolism/rules/slNNN_your_rule_name.py`. It is discovered
automatically. Most rules transform one subschema at a time:

```python
from __future__ import annotations

from typing import Any

from toolism.rules.base import RuleContext, Safety, SchemaRule
from toolism.rules.registry import register_rule
from toolism.schema import SchemaPath


@register_rule
class YourRuleName(SchemaRule):
    """Explain *why* the change is safe. This docstring becomes the rule's docs."""

    id = "SL010"
    name = "your-rule-name"
    summary = "One line: what it removes"
    safety = Safety.SAFE

    def transform(self, node: dict[str, Any], path: SchemaPath, ctx: RuleContext) -> Any:
        # `node` is a fresh dict: delete or set its keys and return it, or return it untouched.
        # Values in non-schema positions (`enum` lists, `required` arrays...) are shared with
        # the input: replace them (node["required"] = [...]), never mutate them in place.
        return node
```

- **Pick the id band by safety.** `SL0xx` for `Safety.SAFE` (validation-equivalent, and removes
  only information the model already has), `SL1xx` for `Safety.LOSSY_ANNOTATIONS`
  (validation-equivalent but drops useful annotation text), `SL2xx` for
  `Safety.CHANGES_VALIDATION`. Registration rejects a mismatch.
- **Walk only real subschemas.** `transform` is called for every subschema (post-order) and
  never inside `enum`, `const`, `default` or `examples` values. Use
  `toolism.schema.property_name(path)` to see whether a node is a property.
- **When in doubt, don't.** Respect `ctx.strict` (OpenAI strict mode) and `ctx.dialect`, and
  leave anything unusual alone. A rule that skips an edge case costs a few tokens; a rule that
  changes validation breaks someone's agent.
- Rules that need the whole tool (description, `$defs`) subclass `ToolRule`. Options are declared
  with `option_specs = {"max_chars": OptionSpec(int, 300, "help")}`.

**2. Add fixtures** in `tests/fixtures/rules/SL010.json`. That's a list of cases, each with
`name`, `tool` (`name`, `description`, `schema`), and the expected `expected_schema` /
`expected_description`. Omitting them means "must be unchanged". Include at least one case that
must *not* change. The runner checks:

- the exact output, including key order
- that the input is never mutated
- that the same object is returned when nothing changes
- idempotence
- bidirectional validation equivalence of every change

```bash
uv run pytest tests/test_rules.py -k SL010
uv run pytest -m slow tests/test_bench.py      # your rule on 159 real tools
```

**3. Regenerate the docs** and note the change:

```bash
uv run toolism rules -f markdown -o docs/rules.md   # a test fails if this is stale
```

Then add a line under `## [Unreleased]` in `CHANGELOG.md`, and if the rule saves tokens on the
corpus, rerun `uv run python bench/run.py` and commit `bench/RESULTS.md` and `results.json`.

## Ideas to work on

Each of these makes a good first issue:

- **SL002, hoisting type-specific keywords.**
  `anyOf: [{type: string, format: date}, {type: null}]` is equivalent to
  `{type: [string, null], format: date}`, because `format`, `pattern`, `minLength` and
  `maxLength` only constrain strings. The Python SDK emits this for every `date | None`.
- **Single-branch `allOf`.** `allOf: [X]` → `X`, when the sibling keywords don't conflict.
- **SL103 `drop-titles` (lossy).** Drop every `title`, not just redundant ones.
- **SL104 `dedupe-descriptions` (lossy).** Drop property descriptions repeated verbatim in the
  tool description.
- **Gemini tokenizer.** `gemini:<model>` using the `count_tokens` API, mirroring
  `tokenizers/anthropic_tokenizer.py`.
- **More formats.** Gemini function declarations; LangChain and LlamaIndex tool exports.
- **HTTP MCP servers.** A loader for streamable-HTTP servers (`--mcp-url`).
- **`toolism diff`.** Show per-tool schema diffs of what slimming changed.
- **More benchmark servers.** Add an entry to `bench/servers.toml`, then run
  `uv run python bench/collect.py <name>` and `bench/run.py`.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat(rules): …`,
  `fix(cli): …`, `docs: …`, `test: …`, `ci: …`.
- Keep one logical change per pull request, with tests. Behaviour changes need a CHANGELOG line.
- Changing the public API (anything in `toolism.__all__`, a CLI option, an exit code, an Action
  input or output) follows the [stability policy](README.md#stability): add the new behaviour,
  keep the old one working with a `DeprecationWarning` for at least one minor release, and list
  both under `### Deprecated` in the CHANGELOG. Never reuse a rule id or make a rule less safe.
- Never claim a saving you didn't measure: numbers in docs come from `bench/RESULTS.md`.

## Releasing (maintainers)

1. Set `__version__` in `src/toolism/__about__.py` and move the CHANGELOG's `Unreleased`
   section under the new version.
2. Commit, tag `vX.Y.Z` and push the tag. The `Release` workflow checks that the tag matches
   `__version__`, builds, publishes to PyPI with trusted publishing, and creates the GitHub
   release from the CHANGELOG.
3. The GitHub Action is versioned by the same tag (`uses: rakeshnitb23/toolism@vX.Y.Z`).

**One-time setup.** On pypi.org, add a trusted publisher: owner `rakeshnitb23`, repository
`toolism`, workflow `release.yml`, environment `pypi`. Then create a `pypi` environment in the
repository settings. No API token is stored anywhere.
