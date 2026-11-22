"""toolism — measure and safely slim the token cost of LLM tool and MCP schemas.

>>> import toolism
>>> toolset = toolism.load("tools.json")            # OpenAI, Anthropic or MCP JSON
>>> toolism.measure(toolset, tokenizer="heuristic").total
>>> result = toolism.slim(toolset, level="safe")
>>> toolism.dump(result.toolset, "tools.slim.json")  # same format and envelope as the input
>>> tools = toolism.slim_tools(my_tool_dicts)         # one call for agent code

Attributes load lazily, so ``import toolism`` stays cheap.
"""

from __future__ import annotations

import importlib
import json
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from toolism.__about__ import __version__

if TYPE_CHECKING:
    from toolism.errors import (
        BaselineError,
        FormatError,
        LoaderError,
        MissingExtraError,
        RuleError,
        TokenizerError,
        TokenizerUnavailable,
        ToolismError,
    )
    from toolism.formats import parse, serialize
    from toolism.io import dump, dumps, load, loads
    from toolism.loaders.mcp import load_mcp_http, load_mcp_stdio
    from toolism.measurement import TokenReport, measure
    from toolism.models import ToolDef, ToolFormat, ToolSet
    from toolism.pipeline import SlimReport, SlimResult, slim
    from toolism.rules import Rule, Safety

__all__ = [
    "BaselineError",
    "FormatError",
    "LoaderError",
    "MissingExtraError",
    "Rule",
    "RuleError",
    "Safety",
    "SlimReport",
    "SlimResult",
    "TokenReport",
    "TokenizerError",
    "TokenizerUnavailable",
    "ToolDef",
    "ToolFormat",
    "ToolSet",
    "ToolismError",
    "__version__",
    "dump",
    "dumps",
    "list_rules",
    "load",
    "load_mcp_http",
    "load_mcp_stdio",
    "loads",
    "measure",
    "parse",
    "serialize",
    "slim",
    "slim_tools",
]

_LAZY = {
    "BaselineError": "toolism.errors",
    "FormatError": "toolism.errors",
    "LoaderError": "toolism.errors",
    "MissingExtraError": "toolism.errors",
    "RuleError": "toolism.errors",
    "TokenizerError": "toolism.errors",
    "TokenizerUnavailable": "toolism.errors",
    "ToolismError": "toolism.errors",
    "parse": "toolism.formats",
    "serialize": "toolism.formats",
    "dump": "toolism.io",
    "dumps": "toolism.io",
    "load": "toolism.io",
    "load_mcp_http": "toolism.loaders.mcp",
    "loads": "toolism.io",
    "load_mcp_stdio": "toolism.loaders.mcp",
    "TokenReport": "toolism.measurement",
    "measure": "toolism.measurement",
    "ToolDef": "toolism.models",
    "ToolFormat": "toolism.models",
    "ToolSet": "toolism.models",
    "SlimReport": "toolism.pipeline",
    "SlimResult": "toolism.pipeline",
    "slim": "toolism.pipeline",
    "Rule": "toolism.rules",
    "Safety": "toolism.rules",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'toolism' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value  # later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


def list_rules() -> tuple[type[Rule], ...]:
    """Every available rule class, sorted by id (``rule.id``, ``rule.name``, ``rule.summary``)."""
    from toolism.rules import all_rules

    return all_rules()


@lru_cache(maxsize=128)
def _slim_cached(document: str, settings: str) -> str:
    from toolism.formats import parse, serialize
    from toolism.pipeline import slim

    result = slim(parse(json.loads(document)), **json.loads(settings))
    return json.dumps(serialize(result.toolset), ensure_ascii=False)


def slim_tools(
    tools: Any, *, level: str = "safe", tokenizer: Any = "heuristic", **kwargs: Any
) -> Any:
    """Slim a tool list (or any supported document) and return it in the same shape.

    Meant for agent code that builds its tools on every request: results are cached by input,
    and every call returns fresh objects you may mutate. ``kwargs`` are passed to
    :func:`toolism.slim` (``enable``, ``disable``, ``options``, ``verify``...). The default
    tokenizer is the offline heuristic: the counts only feed the (discarded) report.
    """
    settings = {"level": level, "tokenizer": tokenizer, **kwargs}
    try:
        key = json.dumps(settings, sort_keys=True)
        document = json.dumps(tools, ensure_ascii=False)
    except (TypeError, ValueError):  # e.g. a Tokenizer instance: run uncached
        from toolism.formats import parse, serialize
        from toolism.pipeline import slim

        return serialize(slim(parse(tools), **settings).toolset)
    return json.loads(_slim_cached(document, key))
