"""Count the tokens of a tool set (design C9). Public as ``toolism.measure``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from toolism.models import ToolDef, ToolSet
from toolism.tokenizers import Tokenizer, get_tokenizer

__all__ = ["SourceCount", "SourcesReport", "TokenReport", "ToolCount", "measure", "measure_sources"]


@dataclass(frozen=True)
class ToolCount:
    """Token count for one tool."""

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


@dataclass(frozen=True)
class SourceCount:
    """Token count for one named tool source, such as one server in an MCP client config."""

    name: str
    tokens: int
    tool_count: int


@dataclass(frozen=True)
class SourcesReport:
    """Token cost of several tool sources that a model sees together.

    ``total`` counts every source's tools as one list, which is what the model receives, so it
    can differ slightly from the sum of the per-source counts.
    """

    tokenizer: str
    approximate: bool
    total: int
    tool_count: int
    sources: tuple[SourceCount, ...]
    not_measured: tuple[tuple[str, str], ...] = ()
    """``(name, reason)`` for each source that failed to load or was skipped."""
    uncounted: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def measure_sources(
    sources: Sequence[tuple[str, ToolSet]],
    tokenizer: str | Tokenizer | None = None,
    *,
    not_measured: Sequence[tuple[str, str]] = (),
) -> SourcesReport:
    """Measure each named tool set, and all of them together as the combined total.

    ``not_measured`` is passed through to the report so it can list sources that never loaded.
    """
    counter = get_tokenizer(tokenizer)
    counts: list[SourceCount] = []
    combined: list[ToolDef] = []
    uncounted: list[str] = []
    warnings: list[str] = []
    for name, toolset in sources:
        function_tools = toolset.function_tools()
        counts.append(SourceCount(name, counter.count_tools(function_tools), len(function_tools)))
        combined.extend(function_tools)
        uncounted.extend(f"{name}: {t.name}" for t in toolset.tools if t.opaque)
        warnings.extend(f"{name}: {w}" for w in toolset.warnings)
    return SourcesReport(
        tokenizer=counter.name,
        approximate=counter.approximate,
        total=counter.count_tools(combined),
        tool_count=len(combined),
        sources=tuple(counts),
        not_measured=tuple(not_measured),
        uncounted=tuple(uncounted),
        warnings=tuple(warnings),
    )
