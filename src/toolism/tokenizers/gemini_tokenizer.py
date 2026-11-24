"""Ground-truth tool token counts from Google's Gemini ``count_tokens`` API."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from toolism.__about__ import __version__
from toolism.errors import TokenizerUnavailable
from toolism.models import ToolDef
from toolism.tokenizers._cache import SnapshotCache

__all__ = ["GeminiTokenizer"]

_CONTENTS = "."
_COUNT_RECIPE = 1


def _persistent_cache(client: Any) -> SnapshotCache | None:
    directory = os.environ.get("TOOLISM_CACHE_DIR")
    if not directory:
        return None
    try:
        # The SDK resolves environment and programmatic endpoint overrides at construction.
        # Use only routing fields. Its HTTP options also contain credential headers.
        api = client._api_client
        options = api.get_read_only_http_options()
        endpoint = options["base_url"]
        api_version = options["api_version"]
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not api_version
        ):
            return None
        namespace = json.dumps(
            [
                "gemini",
                __version__,
                version("google-genai"),
                _COUNT_RECIPE,
                client.vertexai,
                endpoint,
                api_version,
                api.project,
                api.location,
            ]
        )
        return SnapshotCache(Path(directory).expanduser().resolve(), namespace)
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        PackageNotFoundError,
    ):
        return None


def _python_version() -> tuple[int, int]:
    return int(sys.version_info.major), int(sys.version_info.minor)


def to_gemini_tool(tool: ToolDef) -> dict[str, Any]:
    """Convert a tool to the Gemini function declaration shape."""
    declaration: dict[str, Any] = {"name": tool.name}
    if tool.description is not None:
        declaration["description"] = tool.description
    if isinstance(tool.input_schema, dict):
        declaration["parameters_json_schema"] = tool.input_schema
    return declaration


class GeminiTokenizer:
    """Counts tool declarations with ``client.models.count_tokens``."""

    approximate = False
    local = False

    def __init__(self, model: str, client: Any = None) -> None:
        self.model = model
        self.name = f"gemini:{model}"
        self._client = client if client is not None else self._make_client()
        self._persistent = _persistent_cache(self._client) if client is None else None
        self._cache: dict[str, int] = {}
        self._baseline: int | None = None

    @staticmethod
    def _make_client() -> Any:
        if _python_version() < (3, 11):
            raise TokenizerUnavailable(
                "the gemini tokenizer needs Python 3.11 or newer for full JSON Schema support",
                hint="use Python 3.11+ and install 'toolism[gemini]'",
            )
        try:
            from google import genai
        except ImportError as exc:
            raise TokenizerUnavailable(
                "the gemini tokenizer needs the google-genai package",
                hint="pip install 'toolism[gemini]'",
            ) from exc
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            raise TokenizerUnavailable(
                "GEMINI_API_KEY or GOOGLE_API_KEY is not set",
                hint="set GEMINI_API_KEY or pick another tokenizer",
            )
        return genai.Client()

    def _count(self, tools: list[dict[str, Any]]) -> int:
        kwargs: dict[str, Any] = {"model": self.model, "contents": _CONTENTS}
        if tools:
            kwargs["config"] = {"tools": [{"function_declarations": tools}]}
        try:
            return int(self._client.models.count_tokens(**kwargs).total_tokens)
        except Exception as exc:
            raise TokenizerUnavailable(f"Gemini count_tokens failed: {exc}") from exc

    def _baseline_tokens(self) -> int:
        if self._baseline is None:
            self._baseline = self._count([])
        return self._baseline

    def _cached_count(self, cache: SnapshotCache, tools: list[dict[str, Any]]) -> int:
        generation = cache.generation
        key = cache.key("count_tokens", [self.model, _CONTENTS, tools])
        value = cache.get(key)
        if value is None:
            value = self._count(tools)
            cache.put(key, value, generation=generation)
        return value

    def _cached_difference(self, cache: SnapshotCache, tools: list[dict[str, Any]]) -> int:
        generation = cache.generation
        total = self._cached_count(cache, tools)
        baseline = self._cached_count(cache, [])
        if cache.generation != generation:
            # The two counts must not come from opposite sides of a snapshot expiry.
            return self._count(tools) - self._count([])
        return total - baseline

    def count_tool(self, tool: ToolDef) -> int:
        if tool.opaque:
            return 0
        if self._persistent is not None:
            return max(self._cached_difference(self._persistent, [to_gemini_tool(tool)]), 0)
        key = tool.fingerprint()
        if key not in self._cache:
            marginal = self._count([to_gemini_tool(tool)]) - self._baseline_tokens()
            self._cache[key] = max(marginal, 0)
        return self._cache[key]

    def count_tools(self, tools: Sequence[ToolDef]) -> int:
        items = [to_gemini_tool(tool) for tool in tools if not tool.opaque]
        if not items:
            return 0
        if self._persistent is not None:
            return self._cached_difference(self._persistent, items)
        return self._count(items) - self._baseline_tokens()

    def __repr__(self) -> str:
        return f"<GeminiTokenizer {self.name}>"
