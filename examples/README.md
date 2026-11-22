# Examples

Run commands from the repository root with the project environment installed and
activated. The development setup in [CONTRIBUTING.md](../CONTRIBUTING.md) uses
`uv sync --all-extras`.

## Scripts

- `agent_startup.py` loads `anthropic_tools.json`, slims the definitions once,
  and prints their heuristic token counts. `python examples/agent_startup.py`
  runs locally without an API request.
- `openai_startup.py` does the same with `openai_tools.json`.
  `python examples/openai_startup.py` is also offline.
- `measure_savings.py` loads `mcp_tools.json`, applies the safe rules, and
  prints before-and-after heuristic counts and the percentage saved:
  `python examples/measure_savings.py`.

## Tool definition files

The JSON files show three input shapes accepted by `toolism measure`:

- `mcp_tools.json` contains MCP tool definitions.
- `openai_tools.json` contains OpenAI Chat Completions function tools.
- `anthropic_tools.json` contains Anthropic tool definitions.

Measure each sample with the offline heuristic tokenizer:

```bash
toolism measure examples/mcp_tools.json --tokenizer heuristic
toolism measure examples/openai_tools.json --tokenizer heuristic
toolism measure examples/anthropic_tools.json --tokenizer heuristic
```

The heuristic counts are approximate. `toolism measure` detects each JSON
format; selecting `heuristic` keeps these commands local and does not call a
provider API.

## Optional API calls

Both startup scripts support `--call`. Without that option, they only process
the local JSON files. With it, `agent_startup.py` sends a request to Anthropic
and `openai_startup.py` sends one to OpenAI. Set the matching API key first:

```bash
ANTHROPIC_API_KEY=... python examples/agent_startup.py --call
OPENAI_API_KEY=... python examples/openai_startup.py --call
```

These commands make real provider requests and may incur usage charges. The
scripts' default offline runs and the three heuristic measurements above need
no API key.
