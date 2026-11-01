# JSON output

`measure`, `slim`, and `check` can all emit machine-readable reports for CI
scripts and dashboards. The reports are versioned: every one carries
`"toolism_report": 1` and a `"kind"` field naming the report type.

## Version promise

`toolism_report: 1` is the format version, not a tool version. Within version 1:

- Fields may be **added**, but never removed or renamed.
- A removal or rename means a version bump, so a consumer written against
  version 1 keeps parsing.
- `kind` tells you which top-level shape to expect: `measure`, `slim`,
  or `check`.

Token counts are integers everywhere. Averages and percentages are
floats, rounded to 2 decimals. Optional cost estimates are unrounded floats. Percentages in `measure` are per-tool shares
of the total; `slim` percentages are savings shares.

## `measure`

Command:

```sh
toolism measure examples/mcp_tools.json -t heuristic -f json
```

Example (full output, run against `examples/mcp_tools.json` with the
`heuristic` tokenizer):

```json
{
  "toolism_report": 1,
  "kind": "measure",
  "tokenizer": "heuristic",
  "approximate": true,
  "total_tokens": 702,
  "tool_count": 3,
  "average_tokens": 234.0,
  "tools": [
    {
      "name": "create_contact",
      "tokens": 353,
      "percent": 50.28
    },
    {
      "name": "search_contacts",
      "tokens": 176,
      "percent": 25.07
    },
    {
      "name": "log_activity",
      "tokens": 173,
      "percent": 24.64
    }
  ],
  "uncounted": [],
  "warnings": []
}
```

Top-level fields:

| Field | Meaning |
| --- | --- |
| `toolism_report` | Format version, currently `1`. |
| `kind` | Always `"measure"` here. |
| `tokenizer` | Tokenizer used, e.g. `"heuristic"` or `"tiktoken:o200k_base"`. |
| `approximate` | Whether the counts are estimates (`heuristic` is approximate). |
| `total_tokens` | Sum of all counted tools. |
| `tool_count` | Number of tools counted. |
| `average_tokens` | Mean tokens per tool, rounded to 2 decimals. |
| `tools` | One entry per tool: `name`, `tokens`, `percent` (share of the total). Sorted by tokens descending in the table/markdown renderings; the JSON list follows the report order. |
| `uncounted` | Provider built-ins that were not counted, by name. |
| `warnings` | Non-fatal warnings from counting. |

With `measure --price USD_PER_MTOK`, an additive `cost` object contains
`usd_per_million_input_tokens` (the supplied price) and `usd_per_1000_requests`
(`total_tokens / 1000 * price`). It is absent when `--price` is omitted. For example,
1,250 tokens at `--price 3` cost $3.75 per 1,000 requests. Only counted tool-definition
tokens are included, with the same accuracy as the selected tokenizer; provider
wrapping, caching, other input tokens, and output tokens are not included.

## `slim`

`slim` writes the slimmed tool JSON to stdout and the report to stderr, so
the report format is a separate flag:

```sh
toolism slim examples/mcp_tools.json -t heuristic -r json 2> report.json
```

Example report (same input as above; the `rules` array is abbreviated to
two entries, every entry has the same shape):

```json
{
  "toolism_report": 1,
  "kind": "slim",
  "tokenizer": "heuristic",
  "approximate": true,
  "attribution_tokenizer": "heuristic",
  "level": "safe",
  "tool_count": 3,
  "before_tokens": 702,
  "after_tokens": 448,
  "saved_tokens": 254,
  "saved_percent": 36.18,
  "passes": 2,
  "rules": [
    {
      "id": "SL001",
      "name": "redundant-title",
      "tokens_saved": 108,
      "tools_changed": [
        "create_contact",
        "search_contacts",
        "log_activity"
      ]
    },
    {
      "id": "SL002",
      "name": "nullable-anyof",
      "tokens_saved": 56,
      "tools_changed": [
        "create_contact",
        "search_contacts",
        "log_activity"
      ]
    }
  ],
  "tools": [
    {
      "name": "create_contact",
      "before": 353,
      "after": 210,
      "saved": 143
    },
    {
      "name": "search_contacts",
      "before": 176,
      "after": 120,
      "saved": 56
    },
    {
      "name": "log_activity",
      "before": 173,
      "after": 118,
      "saved": 55
    }
  ],
  "uncounted": [],
  "warnings": [],
  "rollbacks": [],
  "verification": []
}
```

Top-level fields:

| Field | Meaning |
| --- | --- |
| `toolism_report` | Format version, currently `1`. |
| `kind` | Always `"slim"` here. |
| `tokenizer` | Tokenizer used for the before/after counts. |
| `approximate` | Whether the counts are estimates. |
| `attribution_tokenizer` | Tokenizer used to attribute savings to individual rules; may differ from `tokenizer`. |
| `level` | Slimming level applied, e.g. `"safe"`. |
| `tool_count` | Number of tools slimmed. |
| `before_tokens` / `after_tokens` | Total tokens before / after slimming. |
| `saved_tokens` / `saved_percent` | Tokens saved and the saving as a percentage of `before_tokens`. |
| `passes` | Number of slimming passes run. |
| `rules` | One entry per rule: `id`, `name`, `tokens_saved`, `tools_changed` (tool names). Rules with no effect appear with `tokens_saved: 0` and an empty `tools_changed`. |
| `tools` | One entry per tool: `name`, `before`, `after`, `saved`. |
| `uncounted` | Provider built-ins that were not counted, by name. |
| `warnings` | Non-fatal warnings from slimming. |
| `rollbacks` | Tools rolled back after verification, each with `tool`, `rules`, `reverted`, `direction`, and `example`. |
| `verification` | Per-tool verification outcomes, each with `tool`, `verdict`, `method`, `direction`, and `reason`. Empty when verification did not run. |

## `check`

Command (a cap or a baseline is required, otherwise there is nothing to
check):

```sh
toolism check examples/mcp_tools.json -t heuristic -f json --max-total 1000
```

Example (passing run):

```json
{
  "toolism_report": 1,
  "kind": "check",
  "ok": true,
  "violations": [],
  "tokenizer": "heuristic",
  "total_tokens": 702,
  "baseline_tokens": null,
  "delta_tokens": null,
  "tools": [],
  "notes": []
}
```

Top-level fields:

| Field | Meaning |
| --- | --- |
| `toolism_report` | Format version, currently `1`. |
| `kind` | Always `"check"` here. |
| `ok` | Whether every budget passed. Also the process exit code: `0` when `ok` is true, `1` when false. |
| `violations` | Human-readable budget failures, e.g. `"total is 702 tokens, over the cap of 100"`. Empty when `ok` is true. |
| `tokenizer` | Tokenizer used for the counts. |
| `total_tokens` | Current total tokens. |
| `baseline_tokens` | Baseline total when a baseline file was given, otherwise `null`. |
| `delta_tokens` | Change vs. the baseline, otherwise `null`. |
| `tools` | Per-tool deltas vs. the baseline: `name`, `before`, `after`, `change`. Empty without a baseline, since there is nothing to diff against. |
| `notes` | Informational notes, e.g. a tokenizer mismatch with the baseline. |

A failing run keeps the same shape with `ok: false`, the violations
listed, and exit code `1`:

```json
{
  "toolism_report": 1,
  "kind": "check",
  "ok": false,
  "violations": [
    "total is 702 tokens, over the cap of 100"
  ],
  "tokenizer": "heuristic",
  "total_tokens": 702,
  "baseline_tokens": null,
  "delta_tokens": null,
  "tools": [],
  "notes": []
}
```
