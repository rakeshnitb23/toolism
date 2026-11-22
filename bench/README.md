# Benchmark workflow

From the repository root, follow [Development setup](../CONTRIBUTING.md#development-setup),
including `uv sync --all-extras`.

## Files and scripts

### `servers.toml`

Lists pinned server packages, versions, and commands. `collect.py` runs them;
`run.py` uses the entries to label results. Add a server here before collecting
its snapshot.

### `collect.py`

Reads `servers.toml` and writes each server's `tools/list` response to
`snapshots/<name>.json`. Pass server names to collect selected servers; omit
them to collect all. `--timeout` sets seconds per server.

```bash
uv run python bench/collect.py server-memory
uv run python bench/collect.py
```

Collection starts the configured MCP servers. `npx` (Node) runs the npm
servers; `uvx` runs the PyPI servers. It may need network access to fetch
uncached packages. The listed servers need no credentials for `tools/list`.

### `run.py`

Reads saved snapshots and `servers.toml`, then writes `results.json` and
`RESULTS.md`:

```bash
uv run python bench/run.py
```

It does not start servers. It loads the `o200k_base` and `cl100k_base`
tiktoken vocabularies, which may download on first use. For offline runs, cache
both in `TIKTOKEN_CACHE_DIR`; if either is unavailable, the run stops before
heuristic counts. If `ANTHROPIC_API_KEY` is set, it also calls Anthropic's
token-count API.

### `make_pydantic.py`

Runs its example MCP server locally using the installed MCP and Pydantic
packages, then writes `snapshots/fastmcp-pydantic.json` without external calls:

```bash
uv run python bench/make_pydantic.py
```

Pass `--serve` to run the example as a stdio server:

```bash
uv run python bench/make_pydantic.py --serve
```

### `snapshots/`

Contains checked-in `tools/list` responses used by `run.py` and
`tests/test_bench.py`. `collect.py` refreshes server snapshots;
`make_pydantic.py` refreshes its own.

### Generated results

`run.py` generates `results.json` and `RESULTS.md`; rerun it to update them.
Do not edit these files by hand.

## Reproduce the benchmark

Run from the repository root in this order:

1. Collect fresh data with `uv run python bench/collect.py`. This may need
   network access, Node/`npx` for npm servers, and `uvx` for PyPI servers.

2. Run `uv run python bench/run.py` on the snapshots. No server starts; see
   the tiktoken and Anthropic notes above for possible network access.

3. Run `uv run pytest -m slow tests/test_bench.py` for schema-equivalence on
   the saved snapshots. It makes no server calls; install the `verify` extra
   or pytest skips these tests.
