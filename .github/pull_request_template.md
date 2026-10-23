## What and why

<!-- One or two sentences. Link the issue: "Closes #N". -->

## Checklist

- [ ] The linked issue is a help wanted issue, or a good first issue assigned to me
- [ ] Tests added or updated (rule changes: fixture cases in `tests/fixtures/rules/`)
- [ ] `uv run ruff check . && uv run mypy && uv run pytest -m "not slow"` pass
- [ ] New or changed rule: `uv run pytest -m slow tests/test_bench.py` passes and `docs/rules.md` is regenerated
- [ ] CHANGELOG entry under `Unreleased` for user-visible changes
- [ ] Public API, CLI options, exit codes or Action inputs changed: old behaviour deprecated, not removed (see the Stability section in README.md)
