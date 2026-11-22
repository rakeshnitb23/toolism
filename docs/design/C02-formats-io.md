# C2 — Formats & IO

## Responsibility

Turn real-world JSON into a `ToolSet` and back, without losing anything the user didn't ask to change.

## Interface

```python
# formats.py
def parse(document: Any) -> ToolSet          # document = already-parsed JSON
def serialize(toolset: ToolSet) -> Any       # returns a new JSON-able object
def detect_item_format(item: Mapping) -> ToolFormat | None   # None = opaque / unknown

# io.py
def load(source: str | os.PathLike | IO[str]) -> ToolSet      # "-" = stdin
def loads(text: str) -> ToolSet
def dumps(toolset: ToolSet, *, indent: int | None = 2) -> str
def dump(toolset: ToolSet, target: str | os.PathLike | IO[str], *, indent: int | None = 2) -> None
```

## Locating the tool list (`path`)

Checked in order:

1. Top-level JSON array → `()`.
2. Object with `"tools"` array → `("tools",)`. Covers MCP `tools/list` results and request bodies such as `{"model":…, "tools":[…]}`.
3. JSON-RPC response `{"result":{"tools":[…]}}` → `("result","tools")`.
4. Object with `"functions"` array (legacy OpenAI) → `("functions",)`.
5. Bedrock Converse request `{"toolConfig":{"tools":[…]}}` → `("toolConfig","tools")`.
6. Otherwise `FormatError("no tool list found …")`.

## Item format detection

A Converse request identifies the format through `toolConfig.tools`. Each item must carry
one of `toolSpec`, `cachePoint` or `systemTool`. The table gives the detection order for other inputs.

| Condition | Format |
|---|---|
| `"function"` is an object with a `"name"` | OPENAI_CHAT |
| has `"inputSchema"` | MCP |
| has `"input_schema"` | ANTHROPIC |
| has `"name"` and `"parameters"` (or `"type":"function"` + `"name"`) | OPENAI_RESPONSES |
| has `"type"` but none of the above (e.g. `web_search_preview`, `bash_20250124`) | opaque |
| has `"name"` only or with other metadata | OPENAI_RESPONSES (legacy function) |
| bare list item with a Bedrock `toolSpec` object | BEDROCK |
| bare list item with a Bedrock `cachePoint` or `systemTool` object | opaque (BEDROCK) |
| otherwise | `FormatError` listing the item's keys |

- All non-opaque items must share one format, otherwise `FormatError` naming both formats and indices.
- Bedrock markers retain the BEDROCK format even in a list with no `toolSpec`. Other
  all-opaque sets get OPENAI_RESPONSES as a label.

**Field mapping** (read from and write back to the same place):

| Format | name | description | schema | strict |
|---|---|---|---|---|
| OPENAI_CHAT | `function.name` | `function.description` | `function.parameters` | `function.strict` |
| OPENAI_RESPONSES | `name` | `description` | `parameters` | `strict` |
| ANTHROPIC | `name` | `description` | `input_schema` | `strict` |
| MCP | `name` | `description` | `inputSchema` | — |
| BEDROCK | `toolSpec.name` | `toolSpec.description` | `toolSpec.inputSchema.json` | `toolSpec.strict` |

## Serialization

1. Deep-copy `toolset.document`.
2. For each tool, deep-copy `tool.source` and patch the three fields in place (order kept):
   - value `None` and key present → delete the key
   - value set → assign (existing position, or appended)
3. Opaque tools are emitted as their unchanged `source`.
4. Put the list back at `path`.

**`dumps`:** `json.dumps(..., indent=indent, ensure_ascii=False)` + trailing newline. `indent=None` → compact separators.

**IO errors:**
- Invalid JSON → `FormatError` with line/column.
- Non-UTF-8 → `FormatError`.
- `NaN` / `Infinity` rejected (`parse_constant`), because they aren't JSON.

## Tests

- Round trip: `serialize(parse(x)) == x` for every format fixture and every benchmark snapshot.
- Each path shape; mixed-format rejection; opaque passthrough (OpenAI built-ins, Anthropic server tools).
- Deleting a description key on write-back; unicode preserved; stdin loading; bad-JSON error position.

## Review

| Issue | Change |
|---|---|
| A request body like `{"model":…,"tools":[…],"messages":[…]}` is common in logs. Should the whole document round-trip? | Yes — path `("tools",)` covers it, and everything else stays untouched. |
| MCP JSON-RPC envelopes and plain `{"tools":[…]}` both appear in the wild. | Both supported (paths 2 and 3). |
| Ambiguity: an MCP tool could also have `"parameters"`. | Detection order puts `inputSchema` before the `parameters` check, so MCP wins. |
| Duplicate tool names are invalid for providers but exist in hand-written files. | Allowed at parse time with a warning collected on the ToolSet (`warnings` tuple). The pipeline matches by index, not name, so duplicates are harmless. |
