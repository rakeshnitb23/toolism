"""Count the tokens of a tool set (design C9). Public as ``toolism.measure``."""

from __future__ import annotations

from dataclasses import dataclass

from toolism.models import ToolSet
from toolism.tokenizers import Tokenizer, get_tokenizer

__all__ = ["TokenReport", "ToolCount", "measure"]


@dataclass(frozen=True)
class ToolCount:
    name: str
    tokens: int


@dataclass(frozen=True)
class TokenReport:
    """Token cost of every countable tool in a tool set."""

    tokenizer: str
    approximate: bool
    total: int
    tool_count: int
    tools: tuple[ToolCount, ...]
    uncounted: tuple[str, ...] = ()
    """Opaque provider built-in tools that cannot be counted."""
    warnings: tuple[str, ...] = ()

    @property
    def average(self) -> float:
        return self.total / self.tool_count if self.tool_count else 0.0


def measure(toolset: ToolSet, tokenizer: str | Tokenizer | None = None) -> TokenReport:
    """Measure every non-opaque tool with ``tokenizer`` (default: ``tiktoken:o200k_base``)."""
    counter = get_tokenizer(tokenizer)
    function_tools = toolset.function_tools()
    return TokenReport(
        tokenizer=counter.name,
        approximate=counter.approximate,
        total=counter.count_tools(function_tools),
        tool_count=len(function_tools),
        tools=tuple(ToolCount(t.name, counter.count_tool(t)) for t in function_tools),
        uncounted=tuple(t.name for t in toolset.tools if t.opaque),
        warnings=toolset.warnings,
    )
