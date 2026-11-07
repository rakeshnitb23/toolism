# Contributing to toolism

Thanks for helping make tool definitions cheaper. New rules, tokenizers, input formats and
benchmark servers are all welcome, as are bug reports with a tool definition that toolism gets
wrong.

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By taking
part, you agree to uphold it.

## Picking an issue

You don't need to be assigned. Pick an issue and open a pull request.

| Label | Who can take it | How |
| --- | --- | --- |
| [good first issue](https://github.com/rakeshnitb23/toolism/labels/good%20first%20issue) | People new to toolism: **fewer than 5 merged PRs here** | Open a PR. A short comment on the issue first ("working on this") is welcome but not required |
| [help wanted](https://github.com/rakeshnitb23/toolism/labels/help%20wanted) | Anyone | Same, but share your plan on the issue first for design questions |

- **First PR wins.** If several people open PRs for the same issue, the first one that's ready
  (CI green and following this guide) is reviewed first. Others may be closed as duplicates, so
  check the issue's linked PRs before starting.
- **Open a draft PR early** if the work takes more than a day, so others can see it's taken.
- **Link the issue** in your PR description: `Closes #N`.
- **5 or more merged PRs?** Please leave good first issues to newcomers and pick a help wanted
  issue instead.
- A bot answers questions on these issues with the rules above, and notes on a PR when another
  open PR already targets the same issue. It never closes anything; maintainers decide.

## Points and levels

Contributions earn points on the [🏆 Contributor leaderboard](https://github.com/rakeshnitb23/toolism/issues?q=is%3Aissue+is%3Aopen+%22Contributor+leaderboard%22+in%3Atitle),
which a bot updates whenever a PR is merged (and every Monday). When your PR is merged, the bot
comments on it with the points you earned, your rank and your level. The top 10 this month also
appear on the README. There's a monthly board, so newcomers always have a race they can win, and
an all-time one.

| Contribution | Points |
| --- | ---: |
| Merged PR that closes a **help wanted** issue | 30 |
| Merged PR that closes a **good first issue** | 10 |
| Any other merged PR | 5 |
| Your first merged PR ever | +5 bonus |
| A **written** review of someone else's PR (one per PR) | 3 |
| A bug report a maintainer confirms (labelled `bug`, closed as completed) | 5 |
| A maintainer bonus for standout work (a `points: +N` label on the PR) | +N |

- **Only merged or confirmed work counts.** Closed, duplicate and unmerged PRs score nothing.
- **Points follow the issue's label, not the size of the change**, so splitting or padding a PR
  earns nothing extra. Link the issue with `Closes #N` so the bot can find its label.
- **A review needs words.** A bare "Approve" with no text doesn't count; say what you checked.
- Bots aren't counted, and maintainers are listed separately rather than ranked.

| Level | Reached at |
| --- | --- |
| 🌱 Newcomer | 1 merged PR |
| 🔧 Contributor | 3 merged PRs |
| ⭐ Regular | 5 merged PRs (time to move from good first issues to help wanted) |
| 🏅 Core | 10 merged PRs and 10 reviews (maintainers look here first when inviting new maintainers) |
| 👋 Supporter | Reviews or bug reports, no merged PR yet |

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
uvx typos                                      # spelling (same check as CI)
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
- **SL104 `dedupe-descriptions` (lossy).** Drop property descriptions repeated verbatim in the
  tool description.
- **Gemini tokenizer.** `gemini:<model>` using the `count_tokens` API, mirroring
  `tokenizers/anthropic_tokenizer.py`.
- **More formats.** Gemini function declarations; LangChain and LlamaIndex tool exports.
- **HTTP MCP servers.** A loader for streamable-HTTP servers (`--mcp-url`).
- **`toolism diff`.** Show per-tool schema diffs of what slimming changed.
- **More benchmark servers.** Add an entry to `bench/servers.toml`, then run
  `uv run python bench/collect.py <name>` and `bench/run.py`. See the
  [benchmark workflow](bench/README.md) for details.

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

1. Open a pull request (`master` is protected) that sets `__version__` in
   `src/toolism/__about__.py`; updates `version` and `date-released` in `CITATION.cff`; moves the
   CHANGELOG's `Unreleased` section under the new version with a short `Highlights` list, and
   updates the compare links at the end of `CHANGELOG.md`; updates the version in the README's
   Action, pre-commit and cache examples; and regenerates the benchmark with
   `uv run python bench/run.py`.
2. After it merges, tag that commit `vX.Y.Z` and push the tag. The `Release` workflow checks that
   the tag matches `__version__`, builds, runs the wheel once, publishes to PyPI with trusted
   publishing, and creates the GitHub release from the CHANGELOG plus GitHub's generated list of
   pull requests and new contributors. A PyPI version can't be replaced, so check before tagging.
3. The GitHub Action is versioned by the same tag (`uses: rakeshnitb23/toolism@vX.Y.Z`).

**One-time setup.** On pypi.org, add a trusted publisher: owner `rakeshnitb23`, repository
`toolism`, workflow `release.yml`, environment `pypi`. Then create a `pypi` environment in the
repository settings. No API token is stored anywhere.
