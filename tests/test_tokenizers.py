from __future__ import annotations

from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from conftest import requires_tiktoken
from toolism.errors import TokenizerError, TokenizerUnavailable
from toolism.models import ToolDef
from toolism.tokenizers import (
    DEFAULT_TOKENIZER,
    ENV_VAR,
    AnthropicTokenizer,
    HeuristicTokenizer,
    TiktokenTokenizer,
    Tokenizer,
    get_tokenizer,
)
from toolism.tokenizers import base as base_module
from toolism.tokenizers.anthropic_tokenizer import to_anthropic_tool

TOOL = ToolDef(
    name="create_issue",
    description="Create an issue.",
    input_schema={
        "type": "object",
        "properties": {"title": {"type": "string", "title": "Title"}},
        "required": ["title"],
    },
)


# ------------------------------------------------------------------ heuristic


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("hello", 1),
        ("hello world", 2),  # single space joins the next word
        ("get_weather", 2),  # underscore merges
        ("inputSchema", 2),  # camelCase splits
        ("HTTPServer", 2),
        ("12345", 2),  # digit groups of up to 3
        ('{"', 1),
        ('":{"', 1),
        ('"}},"', 2),
        ("a\n\nb", 3),
        ("internationalization", 2),
    ],
)
def test_heuristic_counts(text: str, expected: int) -> None:
    assert HeuristicTokenizer().count_text(text) == expected


def test_heuristic_other_scripts() -> None:
    assert HeuristicTokenizer().count_text("天气预报") == 3


def test_heuristic_is_monotonic_on_appends() -> None:
    tok = HeuristicTokenizer()
    text = TOOL.render()
    for cut in range(0, len(text), 7):
        assert tok.count_text(text[:cut]) <= tok.count_text(text)


def test_text_tokenizer_caches_by_fingerprint_and_skips_opaque(monkeypatch: Any) -> None:
    tok = HeuristicTokenizer()
    calls: list[str] = []
    original = tok.count_text

    def spy(text: str) -> int:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(tok, "count_text", spy)
    assert tok.count_tool(TOOL) == tok.count_tool(TOOL.replace())
    assert len(calls) == 1
    assert tok.count_tool(ToolDef(name="web_search", opaque=True)) == 0
    assert tok.count_tools([TOOL, TOOL]) == 2 * tok.count_tool(TOOL)


def test_text_tokenizer_cache_is_bounded(monkeypatch: Any) -> None:
    monkeypatch.setattr(base_module, "_CACHE_LIMIT", 3)
    tok = HeuristicTokenizer()
    for i in range(10):
        tok.count_tool(ToolDef(name=f"t{i}"))
    assert len(tok._cache) == 3


def test_heuristic_repr_and_protocol() -> None:
    tok = HeuristicTokenizer()
    assert isinstance(tok, Tokenizer)
    assert repr(tok) == "<HeuristicTokenizer heuristic>"
    assert tok.local
    assert tok.approximate


# ------------------------------------------------------------------- tiktoken


@requires_tiktoken
def test_tiktoken_counts_match_encoding() -> None:
    import tiktoken

    tok = TiktokenTokenizer("o200k_base")
    enc = tiktoken.get_encoding("o200k_base")
    assert tok.name == "tiktoken:o200k_base"
    assert tok.count_tool(TOOL) == len(enc.encode(TOOL.render()))
    # special-token text is counted as plain text instead of raising
    assert tok.count_text("<|endoftext|>") > 0


def test_tiktoken_unknown_encoding() -> None:
    with pytest.raises(TokenizerUnavailable, match="unknown tiktoken encoding"):
        TiktokenTokenizer("no_such_encoding")


def test_tiktoken_offline_failure_is_unavailable(monkeypatch: Any) -> None:
    import tiktoken

    def boom(name: str) -> Any:
        raise ConnectionError("blocked")

    monkeypatch.setattr(tiktoken, "get_encoding", boom)
    with pytest.raises(TokenizerUnavailable, match="could not load") as info:
        TiktokenTokenizer("o200k_base")
    assert info.value.hint is not None
    assert "TIKTOKEN_CACHE_DIR" in info.value.hint


# ------------------------------------------------------------------ anthropic


class FakeClient:
    """Counts 100 for the request shell, 5 for the probe, plus 1 token per character of tools."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages = SimpleNamespace(count_tokens=self._count)

    def _count(self, *, model: str, messages: list[Any], tools: list[Any] | None = None) -> Any:
        self.calls += 1
        total = 100
        for tool in tools or []:
            total += (
                5 if tool == {"name": "x", "input_schema": {"type": "object"}} else len(str(tool))
            )
        return SimpleNamespace(input_tokens=total)


def test_anthropic_counts_with_fake_client() -> None:
    client = FakeClient()
    tok = AnthropicTokenizer("claude-test", client=client)
    assert tok.name == "anthropic:claude-test"
    assert not tok.local
    assert not tok.approximate
    item_len = len(str(to_anthropic_tool(TOOL)))
    assert tok.count_tool(TOOL) == item_len - 5
    assert tok.count_tool(TOOL) == item_len - 5  # cached
    assert tok.count_tools([TOOL, ToolDef(name="ws", opaque=True)]) == item_len
    assert tok.count_tools([ToolDef(name="ws", opaque=True)]) == 0
    assert tok.count_tool(ToolDef(name="ws", opaque=True)) == 0
    # probe + tool + baseline + set = 4 calls; cached repeats add none
    assert client.calls == 4
    assert repr(tok) == "<AnthropicTokenizer anthropic:claude-test>"


def test_anthropic_errors_become_unavailable() -> None:
    def broken(**kwargs: Any) -> Any:
        raise RuntimeError("401")

    client = SimpleNamespace(messages=SimpleNamespace(count_tokens=broken))
    tok = AnthropicTokenizer("m", client=client)
    with pytest.raises(TokenizerUnavailable, match="count_tokens failed"):
        tok.count_tools([TOOL])


def test_anthropic_without_key_or_package(monkeypatch: Any) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(TokenizerUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicTokenizer("m")
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)
    with pytest.raises(TokenizerUnavailable, match="anthropic package"):
        AnthropicTokenizer("m")


def test_anthropic_creates_real_client_when_key_set(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    tok = AnthropicTokenizer("m")
    assert tok._client is not None


def test_to_anthropic_tool_sanitizes() -> None:
    item = to_anthropic_tool(ToolDef(name="fs.read file", input_schema=True))
    assert item == {"name": "fs_read_file", "input_schema": {"type": "object"}}


# ---------------------------------------------------------------- get_tokenizer


def test_get_tokenizer_specs(monkeypatch: Any) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert isinstance(get_tokenizer("heuristic"), HeuristicTokenizer)
    tok = HeuristicTokenizer()
    assert get_tokenizer(tok) is tok
    monkeypatch.setenv(ENV_VAR, "heuristic")
    assert isinstance(get_tokenizer(), HeuristicTokenizer)
    assert DEFAULT_TOKENIZER == "tiktoken:o200k_base"


@requires_tiktoken
def test_get_tokenizer_default_is_o200k(monkeypatch: Any) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert get_tokenizer().name == "tiktoken:o200k_base"
    assert get_tokenizer("tiktoken").name == "tiktoken:o200k_base"
    assert get_tokenizer("tiktoken:cl100k_base").name == "tiktoken:cl100k_base"


def test_get_tokenizer_anthropic_and_errors(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert get_tokenizer("anthropic:claude-x").name == "anthropic:claude-x"
    with pytest.raises(TokenizerError, match="needs a model"):
        get_tokenizer("anthropic")
    with pytest.raises(TokenizerError, match="unknown tokenizer") as info:
        get_tokenizer("sentencepiece:foo")
    assert info.value.hint is not None


def test_get_tokenizer_gemini(monkeypatch: Any) -> None:
    from toolism.tokenizers import GeminiTokenizer
    from toolism.tokenizers.gemini_tokenizer import to_gemini_tool

    calls: list[dict[str, Any]] = []

    def count_tokens(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(total_tokens=17)

    client = SimpleNamespace(models=SimpleNamespace(count_tokens=count_tokens))
    monkeypatch.setattr(GeminiTokenizer, "_make_client", staticmethod(lambda: client))
    tok = get_tokenizer("gemini:gemini-test")
    assert isinstance(tok, GeminiTokenizer)
    assert tok.name == "gemini:gemini-test"
    assert not tok.local and not tok.approximate
    assert tok.count_tools([TOOL, ToolDef(name="opaque", opaque=True)]) == 17
    assert calls[0]["model"] == "gemini-test"
    assert calls[0]["contents"] == "."
    assert calls[0]["config"]["tools"][0]["function_declarations"][0]["name"] == "create_issue"
    assert calls[0]["config"]["tools"][0]["function_declarations"][0] == {
        "name": "create_issue",
        "description": "Create an issue.",
        "parameters": TOOL.input_schema,
    }
    assert to_gemini_tool(ToolDef(name="minimal")) == {"name": "minimal"}


def test_gemini_setup_errors(monkeypatch: Any) -> None:
    from toolism.tokenizers import GeminiTokenizer

    with monkeypatch.context() as missing_package:
        missing_package.setitem(__import__("sys").modules, "google", None)
        with pytest.raises(TokenizerUnavailable, match="google-genai package") as info:
            GeminiTokenizer("m")
    assert info.value.hint == "pip install 'toolism[gemini]'"

    google = ModuleType("google")
    google.genai = ModuleType("google.genai")  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "google", google)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(TokenizerUnavailable, match="GEMINI_API_KEY or GOOGLE_API_KEY") as info:
        GeminiTokenizer("m")
    assert info.value.hint is not None


def test_gemini_api_failure_becomes_unavailable() -> None:
    from toolism.tokenizers import GeminiTokenizer

    def broken(**kwargs: Any) -> Any:
        raise RuntimeError("401")

    client = SimpleNamespace(models=SimpleNamespace(count_tokens=broken))
    tok = GeminiTokenizer("m", client=client)
    with pytest.raises(TokenizerUnavailable, match="Gemini count_tokens failed: 401"):
        tok.count_tools([TOOL])
