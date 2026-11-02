## What and why

<!-- One or two sentences. Link the issue: "Closes #N". -->

## Checklist

- [ ] Links its issue (`Closes #N`); for a good first issue, I have fewer than 5 merged PRs here
- [ ] Tests added or updated (rule changes: fixture cases in `tests/fixtures/rules/`)
- [ ] `uv run ruff check . && uv run mypy && uv run pytest -m "not slow"` pass
- [ ] New or changed rule: `uv run pytest -m slow tests/test_bench.py` passes and `docs/rules.md` is regenerated
- [ ] Changelog fragment in `changelog.d/` for user-visible changes (see `changelog.d/README.md`)
- [ ] Public API, CLI options, exit codes or Action inputs changed: old behaviour deprecated, not removed (see the Stability section in README.md)
