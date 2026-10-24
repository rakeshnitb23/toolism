"""Print before/after token savings for a tool set with the Python API.

    python examples/measure_savings.py

Loads examples/mcp_tools.json, slims it with the safe rules, and reports the
token cost with the offline heuristic tokenizer: before, after, and the
percentage saved.
"""

from __future__ import annotations

from pathlib import Path

import toolism

HERE = Path(__file__).parent


def main() -> None:
    toolset = toolism.load(HERE / "mcp_tools.json")
    before = toolism.measure(toolset, tokenizer="heuristic").total
    result = toolism.slim(toolset, level="safe", tokenizer="heuristic")
    after = toolism.measure(result.toolset, tokenizer="heuristic").total
    saved = before - after
    pct = result.report.saved_pct
    print(f"tokens before: {before}")
    print(f"tokens after:  {after}")
    print(f"saved:         {saved} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
