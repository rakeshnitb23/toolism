"""Ground-truth counts from Anthropic's ``count_tokens`` endpoint (design C4).

* ``count_tools(T)`` = tokens(request with T) − tokens(request without tools). It includes the fixed
  tool-use preamble once.
* ``count_tool(t)`` = tokens(request with [t]) − tokens(request with a minimal probe tool): the
  *marginal* cost of one tool, excluding the preamble.

Sends tool definitions to Anthropic — only used when the user asks for it explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from toolism.__about__ import __version__
from toolism.errors import TokenizerUnavailable
from toolism.models import ToolDef
from toolism.tokenizers._cache import SnapshotCache

__all__ = ["AnthropicTokenizer"]

_PROBE: dict[str, Any] = {"name": "x", "input_schema": {"type": "object"}}
_MESSAGES = [{"role": "user", "content": "."}]
_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_COUNT_RECIPE = 1


def _persistent_cache(model: str, client: Any, injected: bool) -> SnapshotCache | None:
    directory = os.environ.get("TOOLISM_CACHE_DIR")
    if injected or not directory or os.environ.get("ANTHROPIC_CUSTOM_HEADERS"):
        return None
    try:
        endpoint = str(client.base_url)
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        selected: dict[str, str] = {}
        for key, value in client.default_headers.items():
            name = key.lower()
            if name in {"anthropic-version", "anthropic-beta"}:
                if not isinstance(value, str) or (name in selected and selected[name] != value):
                    return None
                selected[name] = value
        if "anthropic-version" not in selected:
            return None
        identity = [
            "anthropic",
            model,
            __version__,
            version("anthropic"),
            _COUNT_RECIPE,
            endpoint,
            selected,
            _MESSAGES,
            _PROBE,
        ]
        namespace = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
        try:
            location = Path(directory).expanduser().resolve()
        except RuntimeError:
            return None
        return SnapshotCache(location, namespace)
    except (OSError, ValueError, TypeError, AttributeError, PackageNotFoundError):
        return None


def anthropic_tool_name(name: str) -> str:
    """The name as Anthropic accepts it: other characters become ``_``, cut at 128 characters."""
    return _INVALID_NAME_CHARS.sub("_", name)[:128] or "tool"


def to_anthropic_tool(tool: ToolDef) -> dict[str, Any]:
    """Convert any tool to the Anthropic wire shape used for counting."""
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {"type": "object"}
    item: dict[str, Any] = {"name": anthropic_tool_name(tool.name)}
    if tool.description is not None:
        item["description"] = tool.description
    item["input_schema"] = schema
    return item


class AnthropicTokenizer:
    """Counts with ``client.messages.count_tokens`` for a given Claude model."""

    approximate = False
    local = False

    def __init__(self, model: str, client: Any = None) -> None:
        self.model = model
        self.name = f"anthropic:{model}"
        self._client = client if client is not None else self._make_client()
        self._cache: dict[str, int] = {}
        self._baseline: int | None = None
        self._probe: int | None = None
        self._persistent = _persistent_cache(model, self._client, client is not None)

    @staticmethod
    def _make_client() -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise TokenizerUnavailable(
                "the anthropic tokenizer needs the anthropic package",
                hint="pip install 'toolism[anthropic]'",
            ) from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise TokenizerUnavailable(
                "ANTHROPIC_API_KEY is not set", hint="export ANTHROPIC_API_KEY=... or pick another"
            )
        return anthropic.Anthropic()

    def _count(self, tools: list[dict[str, Any]]) -> int:
        kwargs: dict[str, Any] = {"model": self.model, "messages": _MESSAGES}
        if tools:
            kwargs["tools"] = tools
        try:
            return int(self._client.messages.count_tokens(**kwargs).input_tokens)
        except Exception as exc:
            raise TokenizerUnavailable(f"Anthropic count_tokens failed: {exc}") from exc

    def _baseline_tokens(self) -> int:
        if self._persistent is not None:
            return self._cached_count("baseline", [], [])
        if self._baseline is None:
            self._baseline = self._count([])
        return self._baseline

    def _probe_tokens(self) -> int:
        if self._persistent is not None:
            return self._cached_count("probe", _PROBE, [_PROBE])
        if self._probe is None:
            self._probe = self._count([_PROBE])
        return self._probe

    def _cached_count(self, kind: str, identity: Any, tools: list[dict[str, Any]]) -> int:
        cache = self._persistent
        assert cache is not None
        generation = cache.generation
        key = cache.key(kind, identity)
        cached = cache.get(key)
        if cached is not None:
            return cached
        value = self._count(tools)
        cache.put(key, value, generation=generation)
        return value

    def count_tool(self, tool: ToolDef) -> int:
        if tool.opaque:
            return 0
        key = tool.fingerprint()
        cache = self._persistent
        if cache is not None:
            wire = to_anthropic_tool(tool)
            persistent_key = cache.key("marginal", [key, wire])
            for _ in range(2):
                generation = cache.generation
                cached = cache.get(persistent_key)
                if cached is not None:
                    return cached
                marginal = max(self._count([wire]) - self._probe_tokens(), 0)
                if cache.generation == generation:
                    cache.put(persistent_key, marginal, generation=generation)
                    return marginal
            return max(self._count([wire]) - self._count([_PROBE]), 0)
        if key not in self._cache:
            marginal = self._count([to_anthropic_tool(tool)]) - self._probe_tokens()
            self._cache[key] = max(marginal, 0)
        return self._cache[key]

    def count_tools(self, tools: Sequence[ToolDef]) -> int:
        items = [to_anthropic_tool(t) for t in tools if not t.opaque]
        if not items:
            return 0
        cache = self._persistent
        if cache is not None:
            try:
                fingerprints = [tool.fingerprint() for tool in tools if not tool.opaque]
            except (TypeError, ValueError, RecursionError):
                return self._count(items) - self._baseline_tokens()
            for _ in range(2):
                generation = cache.generation
                raw = self._cached_count("bundle", [fingerprints, items], items)
                baseline = self._baseline_tokens()
                if cache.generation == generation:
                    return raw - baseline
            return self._count(items) - self._count([])
        return self._count(items) - self._baseline_tokens()

    def __repr__(self) -> str:
        return f"<AnthropicTokenizer {self.name}>"
