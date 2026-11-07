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

    Each source's ``tokens`` is the sum of its tools' own costs, as in :class:`TokenReport`.
    ``total`` counts every source's tools as one list, which is what the model receives, so it
    also holds the once-per-request overhead some tokenizers add (``anthropic:``).
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


def _unique_names(
    sources: Sequence[tuple[str, Sequence[ToolDef]]],
) -> tuple[list[ToolDef], list[str]]:
    """All the sources' tools as one list, with names made unique so it can be counted as one.

    Providers such as Anthropic reject a request whose tool names repeat, and clients rename
    clashing tools anyway (Claude Code sends ``mcp__<server>__<tool>``). A suffix changes the
    count by a token or two, which is far less than the clash itself would distort it.
    """
    from toolism.tokenizers.anthropic_tokenizer import anthropic_tool_name

    # Compared as Anthropic receives them, so ``fs.read`` and ``fs_read`` clash too.
    owners: dict[str, list[str]] = {}
    for name, tools in sources:
        for tool in tools:
            owners.setdefault(anthropic_tool_name(tool.name), []).append(name)
    combined: list[ToolDef] = []
    taken: set[str] = set()
    for _, tools in sources:
        for tool in tools:
            wire = anthropic_tool_name(tool.name)
            if wire not in taken:
                taken.add(wire)
                combined.append(tool)
                continue
            n = 2
            while f"{wire[:120]}_{n}" in taken:
                n += 1
            unique = f"{wire[:120]}_{n}"
            taken.add(unique)
            combined.append(tool.replace(name=unique))
    warnings = [
        f"tool name {tool!r} is used by several servers ({', '.join(dict.fromkeys(names))})"
        for tool, names in owners.items()
        if len(set(names)) > 1
    ]
    return combined, warnings


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
    function_tools = [(name, toolset.function_tools()) for name, toolset in sources]
    combined, warnings = _unique_names(function_tools)
    for name, toolset in sources:
        warnings.extend(f"{name}: {w}" for w in toolset.warnings)
    return SourcesReport(
        tokenizer=counter.name,
        approximate=counter.approximate,
        total=counter.count_tools(combined),
        tool_count=len(combined),
        sources=tuple(
            SourceCount(name, sum(counter.count_tool(t) for t in tools), len(tools))
            for name, tools in function_tools
        ),
        not_measured=tuple(not_measured),
        uncounted=tuple(
            f"{name}: {t.name}" for name, toolset in sources for t in toolset.tools if t.opaque
        ),
        warnings=tuple(warnings),
    )
