"""Ground-truth tool token counts from Google's Gemini ``count_tokens`` API."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from toolism.errors import TokenizerUnavailable
from toolism.models import ToolDef

__all__ = ["GeminiTokenizer"]


def to_gemini_tool(tool: ToolDef) -> dict[str, Any]:
    """Convert a tool to the Gemini function declaration shape."""
    declaration: dict[str, Any] = {"name": tool.name}
    if tool.description is not None:
        declaration["description"] = tool.description
    if isinstance(tool.input_schema, dict):
        declaration["parameters"] = tool.input_schema
    return declaration


class GeminiTokenizer:
    """Counts tool declarations with ``client.models.count_tokens``."""

    approximate = False
    local = False

    def __init__(self, model: str, client: Any = None) -> None:
        self.model = model
        self.name = f"gemini:{model}"
        self._client = client if client is not None else self._make_client()

    @staticmethod
    def _make_client() -> Any:
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
        kwargs: dict[str, Any] = {"model": self.model, "contents": "."}
        if tools:
            kwargs["config"] = {"tools": [{"function_declarations": tools}]}
        try:
            return int(self._client.models.count_tokens(**kwargs).total_tokens)
        except Exception as exc:
            raise TokenizerUnavailable(f"Gemini count_tokens failed: {exc}") from exc

    def count_tool(self, tool: ToolDef) -> int:
        return 0 if tool.opaque else self._count([to_gemini_tool(tool)])

    def count_tools(self, tools: Sequence[ToolDef]) -> int:
        items = [to_gemini_tool(tool) for tool in tools if not tool.opaque]
        return self._count(items) if items else 0

    def __repr__(self) -> str:
        return f"<GeminiTokenizer {self.name}>"
