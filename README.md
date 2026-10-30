# toolism

[![PyPI](https://img.shields.io/pypi/v/toolism)](https://pypi.org/project/toolism/)
[![Python versions](https://img.shields.io/pypi/pyversions/toolism)](https://pypi.org/project/toolism/)
[![CI](https://github.com/rakeshnitb23/toolism/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/rakeshnitb23/toolism/actions/workflows/ci.yml?query=branch%3Amaster)
[![License](https://img.shields.io/github/license/rakeshnitb23/toolism)](LICENSE)
[![Leaderboard](https://img.shields.io/badge/%F0%9F%8F%86-contributor%20leaderboard-orange)](#top-contributors)

**Your agent pays for its tool definitions on every single request. toolism measures that cost
and cuts it, without changing which tool calls are valid.**

Every tool you give an LLM (OpenAI function, Anthropic tool, MCP server tool) is sent as JSON
Schema with every request. Schema generators such as Pydantic, FastMCP, zod-to-json-schema and
OpenAPI converters pad that JSON with things the model already knows: titles that repeat the
property name, `anyOf` wrappers for optional fields, `default: null`, `$schema` URIs, unused
`$defs`. toolism removes exactly that, proves or tests that the result accepts the same tool
calls, and gives you a CI gate so the cost can't creep back up.

On 14 tool sets (159 tools from 13 popular MCP servers, including the official reference servers,
GitHub, Playwright, Notion, Context7 and AWS docs, plus one generated Pydantic server), the default safe rules cut tool-definition tokens by
**37.4% in total, with a median of 11.3% per tool set**: 22% on the official git server, 34% on
a typical Pydantic-typed server, 73% on Notion (which copies the same nine `$defs` into all 24
tools; 18 of them use none).
Lean, hand-tuned schemas save almost nothing, and the [benchmark](bench/RESULTS.md) shows those
too.

## Install

```bash
pip install toolism                 # CLI + Python API
pip install "toolism[verify]"       # + equivalence verification (hypothesis-jsonschema)
pip install "toolism[mcp]"          # + read tools straight from an MCP server
pip install "toolism[all]"          # everything, incl. Anthropic's exact token counter
```

## 30-second tour

**Measure** what your tools cost (OpenAI, Anthropic and MCP JSON are detected automatically):

```console
$ toolism measure examples/mcp_tools.json
Tool definition tokens
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┓
┃ Tool            ┃ Tokens ┃ % of total ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━┩
│ create_contact  │    354 │      50.0% │
│ log_activity    │    178 │      25.1% │
│ search_contacts │    176 │      24.9% │
└─────────────────┴────────┴────────────┘
708 tokens across 3 tools (average 236) — tiktoken:o200k_base (approximate)
```

**Slim** them. The JSON goes to stdout in the same format and envelope as the input; the report
goes to stderr:

```console
$ toolism slim examples/mcp_tools.json -o tools.slim.json
Slimmed 3 tools: 708 → 457 tokens (−251, −34.6%) — tiktoken:o200k_base (approximate), level safe
Savings by rule
┏━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┓
┃ Rule  ┃ Name                   ┃ Tokens ┃ Tools ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━┩
│ SL001 │ redundant-title        │   −111 │     3 │
│ SL002 │ nullable-anyof         │    −36 │     2 │
│ SL003 │ null-default           │    −33 │     3 │
│ SL004 │ inline-defs            │    −42 │     2 │
│ SL005 │ description-whitespace │     −6 │     3 │
│ SL006 │ echo-description       │    −17 │     1 │
└───────┴────────────────────────┴────────┴───────┘
```

Point it at a live MCP server instead of a file with `--mcp`:

```console
$ toolism measure --mcp "npx -y @modelcontextprotocol/server-github"
```

`--mcp` runs that command exactly as your shell would, with your environment, so only point it
at servers you would run anyway. See [SECURITY.md](SECURITY.md).

For a streamable-HTTP MCP endpoint, use `--mcp-url` on `measure`, `slim` or `check`:

```bash
toolism measure --mcp-url https://your-server.example/mcp
toolism slim --mcp-url http://127.0.0.1:8000/mcp -o tools.slim.json
toolism check --mcp-url http://127.0.0.1:8000/mcp --max-total 10000
```

Give exactly one input: a file/stdin, `--mcp COMMAND`, or `--mcp-url URL`. The URL must be
the server's MCP endpoint. Both live loaders initialize a session and list every page of tools;
they do not execute tools. `--mcp-timeout SECONDS` bounds connection, initialization and listing
together (default: 60 seconds). HTTP loading requires the `mcp` extra and an SDK with streamable
HTTP support; upgrade with `pip install --upgrade "toolism[mcp]"` if needed. The HTTP loader
does not configure OAuth or authentication headers, or the older standalone SSE transport.

Python callers can use `toolism.load_mcp_http(url, timeout=60)`. Inside an event loop:

```python
from toolism.loaders.mcp import load_mcp_http_async

tools = await load_mcp_http_async(url, timeout=60)
```

**Preview** what slimming changes, as a unified diff per changed tool:

```bash
toolism diff examples/mcp_tools.json
toolism diff examples/mcp_tools.json --level annotations -o tools.diff
```

The preview shows normalized JSON for each tool's name, description and input schema.
Unchanged tools are omitted; a tool-order change alone produces no diff. `--level`,
`--enable`, `--disable` and `--option` select the same rewrites as `slim`. The preview runs
locally without a reporting-tokenizer call. Use `-` for stdin and `--output` to write a file.

**Gate** it in CI against a committed baseline:

```console
$ toolism check tools.json -b .toolism/baseline.json --update-baseline   # once, then commit it
Wrote .toolism/baseline.json: 457 tokens across 3 tools (tiktoken:o200k_base).

$ toolism check tools.json -b .toolism/baseline.json --max-increase 5%   # in CI
toolism check FAILED
Total: 708 tokens across 3 tools (baseline 457, +251, +54.9%) — tiktoken:o200k_base (approximate)
  ✗ total grew by 251 tokens (+54.9%), over the allowed 5%
Changed tools
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━┳━━━━━━━━┓
┃ Tool            ┃ Baseline ┃ Now ┃ Change ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━╇━━━━━━━━┩
│ create_contact  │      212 │ 354 │   +142 │
│ search_contacts │      122 │ 176 │    +54 │
│ log_activity    │      129 │ 178 │    +49 │
└─────────────────┴──────────┴─────┴────────┘
```

(Here the baseline was taken from the slimmed tools, and someone then shipped the unslimmed
schemas again.) Without `--max-increase`, any growth over the baseline fails; `--max-total` and
`--max-tool` set absolute caps.

Exit codes: `0` OK, `1` budget exceeded (or a verification rollback with `--fail-on-rollback`),
`2` usage or input error, `3` tokenizer unavailable.

## What it changes

Before, as the MCP Python SDK generates it:

```json
"occurred_on": {"anyOf": [{"format": "date", "type": "string"}, {"type": "null"}],
                "default": null, "title": "Occurred On"},
"priority": {"$ref": "#/$defs/Priority", "default": "normal"}
...
"$defs": {"Priority": {"enum": ["low", "normal", "high"], "title": "Priority", "type": "string"}},
"title": "log_activityArguments"
```

After `toolism slim`, which accepts exactly the same arguments:

```json
"occurred_on": {"anyOf": [{"format": "date", "type": "string"}, {"type": "null"}]},
"priority": {"enum": ["low", "normal", "high"], "type": "string", "default": "normal"}
```

| Rule | What it removes | Default |
| --- | --- | --- |
| SL001 `redundant-title` | titles that repeat the property, `$defs` key or tool name | ✓ |
| SL002 `nullable-anyof` | `anyOf: [{type: X}, {type: null}]` → `type: [X, "null"]` | ✓ |
| SL003 `null-default` | `default: null` on optional properties (kept for strict tools) | ✓ |
| SL004 `inline-defs` | unused `$defs`; inlines definitions used exactly once | ✓ |
| SL005 `description-whitespace` | docstring indentation, trailing spaces, blank-line runs | ✓ |
| SL006 `echo-description` | descriptions that only repeat the property name (`user_id`: "The user ID") | ✓ |
| SL007 `stable-order` | nothing; sorts tools so prompt-cache prefixes stay stable | `--enable` |
| SL008 `noop-keywords` | `required: []`, `additionalProperties: true` and other defaults | ✓ |
| SL009 `schema-uri` | the root `$schema` URI when the schema means the same in every draft | ✓ |
| SL101 `max-description-length` | truncates long descriptions at a sentence boundary | `--level annotations` |
| SL102 `drop-examples` | `examples` / `example` | `--level annotations` |

Every rule is documented, with its exact conditions, in [docs/rules.md](docs/rules.md)
(`toolism rules` lists them too).

### Safety levels and verification

- **`--level safe`** (default): every rule is *validation-equivalent*, meaning the slim schema
  accepts exactly the same tool calls as the original. It removes only information the model
  already has.
- **`--level annotations`**: also drops annotation text that might help the model (examples,
  long descriptions). Validation is still unchanged; measure the effect on your own evals.
- **`--verify`** (`toolism[verify]`) checks every changed schema in both directions. It proves
  equivalence when only annotations changed, and otherwise searches for a counterexample with
  [hypothesis-jsonschema](https://github.com/python-jsonschema/hypothesis-jsonschema). When a
  tool has a counterexample, toolism finds the rules responsible and reverts only their changes;
  if it can't single them out, the whole tool is restored to its original. In CI, every schema the default rules
  change in the [benchmark corpus](bench/) is checked this way.

OpenAI `strict: true` tools are respected: anything strict mode requires (`required` listing every
property, `additionalProperties: false`) is left alone.

## Python API

```python
import toolism

toolset = toolism.load("tools.json")  # or toolism.parse(list_of_dicts)
print(toolism.measure(toolset).total)

result = toolism.slim(toolset, level="safe", verify=True)
print(result.report.saved, result.report.saved_pct)
toolism.dump(result.toolset, "tools.slim.json")  # same format and envelope as the input
```

In an agent, slim once and send the slim tools with every request:

```python
tools = toolism.slim_tools(my_tools)  # same shape back (OpenAI, Anthropic or MCP dicts)
client.messages.create(model=..., tools=tools, messages=...)
```

`slim_tools` is cached by input and returns fresh objects each call. See
[examples/agent_startup.py](examples/agent_startup.py) (Anthropic SDK) and
[examples/openai_startup.py](examples/openai_startup.py) (OpenAI SDK).

### Stability

The public API is every name in `toolism.__all__` (functions such as `load`, `measure`, `slim`
and `slim_tools`, plus the models and errors), the CLI commands and their options, the exit codes,
and the GitHub Action's inputs and outputs. Anything under a leading underscore or not exported
from `toolism` is internal and can change at any time.

Before 1.0, a minor release (0.x → 0.y) may change the public API, but only after the old
behaviour has been deprecated with a warning for at least one minor release, and every such change
is listed in the [CHANGELOG](CHANGELOG.md). Patch releases never change it. Rule ids are never
reused, and a rule's safety level never becomes less safe.

To print the before/after token saving for a file of tools (offline, heuristic
tokenizer), see [examples/measure_savings.py](examples/measure_savings.py).

## GitHub Action

```yaml
# .github/workflows/tool-tokens.yml
on: pull_request
jobs:
  tool-tokens:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: toolism
        uses: rakeshnitb23/toolism@v0.1.0
        with:
          source: tools.json                  # or: mcp: "npx -y @your/mcp-server"
          baseline: .toolism/baseline.json
          max-increase: 5%
```

The result is added to the job summary. Outputs: `status` (`pass`/`fail`/`error`), `total`,
`delta` and `summary-file`. To post the summary as a pull request comment, add a step after it:

```yaml
      - uses: marocchino/sticky-pull-request-comment@v2
        if: always()
        with:
          path: ${{ steps.toolism.outputs.summary-file }}
```

### pre-commit

The same gate runs locally before each commit with [pre-commit](https://pre-commit.com):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/rakeshnitb23/toolism
    rev: vX.Y.Z  # the first release after 0.1.0 includes the hook
    hooks:
      - id: toolism-check
        args: [tools.json, --baseline, .toolism/baseline.json, --max-increase, "5%"]
        files: ^tools\.json$
```

The hook runs whenever a matching file is staged. Create the baseline once with
`toolism check tools.json -b .toolism/baseline.json --update-baseline` and commit it.

## Token counts

| Tokenizer | Use it for |
| --- | --- |
| `tiktoken:o200k_base` (default) | OpenAI models; a good approximation elsewhere |
| `heuristic` | offline, no dependencies; within ~3% of o200k on the benchmark |
| `anthropic:<model>` | exact counts from Anthropic's `count_tokens` API (needs `ANTHROPIC_API_KEY`) |

For machine-readable reports, see [JSON output](docs/json-output.md): `measure -f json`,
`slim -r json`, and `check -f json` share the versioned `toolism_report: 1` format.

The default tokenizer downloads its vocabulary file (a few MB) once on first use. Offline, behind
a proxy that blocks it, or in locked-down CI, use `-t heuristic`, or download it once and point
`TIKTOKEN_CACHE_DIR` at the cache (in GitHub Actions, cache that directory with `actions/cache`).

Counts are taken on a provider-neutral rendering of each tool. Providers wrap tools in their own
prompt format, which adds overhead per tool that no schema change can remove. Reports always name
the tokenizer and say whether the count is approximate. Set a default with `TOOLISM_TOKENIZER`.

## Environment variables

| Variable | Read in | What it does | When unset |
| --- | --- | --- | --- |
| `TOOLISM_TOKENIZER` | `tokenizers/__init__.py` | Default tokenizer when `-t`/`--tokenizer` isn't given (same forms as `-t`) | `tiktoken:o200k_base` |
| `TOOLISM_DEBUG` | `cli.py`, `loaders/mcp.py` | Any non-empty value shows the full traceback instead of a one-line `error:`, and passes an `--mcp` server's stderr through to yours | One-line errors; the server's stderr is captured and hidden |
| `NO_COLOR` | `cli.py` | If set (to any value), terminal output is not coloured | Coloured when writing to a terminal |
| `TIKTOKEN_CACHE_DIR` | tiktoken | Where the `tiktoken:*` vocabulary is cached, so it can be reused offline | tiktoken's default cache in the system temp directory |
| `ANTHROPIC_API_KEY` | `tokenizers/anthropic_tokenizer.py` | Needed for `-t anthropic:<model>` | `anthropic:` tokenizers fail with exit code 3 |
| `GITHUB_STEP_SUMMARY` | `cli.py` | `toolism check` appends its Markdown summary to this file (set by GitHub Actions) | No step summary is written |

A server started with `--mcp` inherits toolism's environment. The GitHub Action also reads its
inputs as `INPUT_*` variables and uses `GITHUB_OUTPUT` and `RUNNER_TEMP`; these are set by the
runner and don't need to be set by hand.

## How it differs

- **Prompt and context compressors** (for example LLMLingua or Headroom) shrink messages, documents
  and tool *results* at runtime, often with a model in the loop. toolism works on tool
  *definitions*, is deterministic, and keeps validation identical. The two approaches complement
  each other.
- **Gateways and proxies** (for example LiteLLM) route requests and track cost; they send your
  schemas as they are. toolism makes those schemas smaller before they leave.
- **Loading fewer tools** (tool search or dynamic selection) cuts the number of tools sent;
  toolism cuts the cost of each tool you do send.

## FAQ

**How much money does this save?** Tokens saved per request × requests ÷ 1,000,000 × your
model's input price per million tokens. For the three tools in the tour above (251 tokens saved
per request), an agent making 1M requests a month on a model priced at, say, $3 per million input
tokens saves 251 × $3 = $753 a month. Use `-t anthropic:<model>` for exact Claude counts, and
check your provider's current prices.

**Doesn't prompt caching make this pointless?** No, it makes it cheaper. Cached tokens still fill
the context window, any change to the tool list misses the cache, and on Anthropic cache writes
cost more than normal input. Cache reads are discounted, not free (Anthropic bills them at a
tenth of the input price), so slimmer tools are cheaper cached and uncached. If your agent assembles tools in a varying order, `--enable SL007`
keeps the cached prefix stable.

**Will the model behave differently?** At the safe level, the model sees the same information,
minus duplicates of what the property names and types already say. Any prompt change can shift
outputs slightly, so if tool-call accuracy matters, compare on your evals. That matters most for
`--level annotations`.

**Is tiktoken accurate for Claude or Gemini?** Not exactly, which is why reports say
"approximate". Use `-t anthropic:<model>` for exact Claude counts. Relative savings are stable
across tokenizers: within about 2 percentage points in the [benchmark](bench/RESULTS.md).

## Top contributors

<a href="https://github.com/rakeshnitb23/toolism/issues?q=is%3Aissue+is%3Aopen+%22Contributor+leaderboard%22+in%3Atitle">
  <img src="https://raw.githubusercontent.com/rakeshnitb23/toolism/leaderboard/leaderboard.svg" alt="Top toolism contributors this month">
</a>

Updated on every merged PR. Merged PRs, written reviews and confirmed bug reports earn points:
see [Points and levels](CONTRIBUTING.md#points-and-levels) and the
[full leaderboard](https://github.com/rakeshnitb23/toolism/issues?q=is%3Aissue+is%3Aopen+%22Contributor+leaderboard%22+in%3Atitle).

## Contributing

New rules, tokenizers and formats are welcome. A rule is one small module plus a fixture file;
see [CONTRIBUTING.md](CONTRIBUTING.md) for the 3-step guide and a list of ideas. Everyone taking
part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
