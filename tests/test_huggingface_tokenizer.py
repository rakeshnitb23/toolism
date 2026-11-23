"""Hugging Face integration tests: use a real tokenizer and never contact the Hub."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from toolism.cli import app
from toolism.tokenizers import ENV_VAR, get_tokenizer

SPEC = "huggingface:example/tiny"
SOURCE = "examples/mcp_tools.json"


@pytest.fixture
def local_tokenizer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    hub = pytest.importorskip("huggingface_hub")
    tokenizers = pytest.importorskip("tokenizers")
    tokenizer = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(
            {"[UNK]": 0, "hello": 1, "world": 2, "[BOS]": 3, "[EOS]": 4}, unk_token="[UNK]"
        )
    )
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.post_processor = tokenizers.processors.TemplateProcessing(
        single="[BOS] $A [EOS]", special_tokens=[("[BOS]", 3), ("[EOS]", 4)]
    )
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    fetches: list[str] = []

    def local_download(repo_id: str, filename: str, *, token: bool) -> str:
        assert repo_id == "example/tiny"
        assert filename == "tokenizer.json"
        assert token is False
        fetches.append(filename)
        return str(path)

    monkeypatch.setattr(hub, "hf_hub_download", local_download)
    return fetches


def test_huggingface_counts_raw_text_without_wrapping_tokens(
    local_tokenizer: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from toolism.tokenizers.base import TextTokenizer

    monkeypatch.setenv(ENV_VAR, SPEC)
    counter = get_tokenizer()
    assert isinstance(counter, TextTokenizer)
    assert counter.name == SPEC
    assert counter.local
    assert counter.approximate
    assert counter.count_text("hello world") == 2
    assert counter.count_text("hello") == 1
    assert local_tokenizer == ["tokenizer.json"]


def test_huggingface_cli_reports_estimated_counts(local_tokenizer: list[str]) -> None:
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", SPEC, "-f", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["tokenizer"] == SPEC
    assert report["approximate"] is True


def test_huggingface_cli_missing_extra_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", SPEC])
    assert result.exit_code == 3, result.output
    assert result.stderr.count("error:") == 1
    assert "toolism[huggingface]" in result.stderr


@pytest.mark.parametrize(
    ("error_name", "message"),
    [
        ("GatedRepoError", "gated"),
        ("RepositoryNotFoundError", "not public"),
        ("EntryNotFoundError", "tokenizer.json"),
        ("LocalEntryNotFoundError", "cache"),
        ("HfHubHTTPError", "could not download"),
    ],
)
def test_huggingface_cli_hub_failure_exits_3_without_leaking_exception(
    monkeypatch: pytest.MonkeyPatch, error_name: str, message: str
) -> None:
    hub = pytest.importorskip("huggingface_hub")
    pytest.importorskip("tokenizers")
    from huggingface_hub import errors

    def error_download(*args: Any, **kwargs: Any) -> str:
        error = getattr(errors, error_name)
        if error_name != "LocalEntryNotFoundError" and issubclass(error, errors.HfHubHTTPError):
            status = {"GatedRepoError": 403, "RepositoryNotFoundError": 404}.get(error_name, 502)
            response = SimpleNamespace(status_code=status, headers={}, request=None, text="")
            raise error("untrusted upstream details", response=response)
        raise error("untrusted upstream details")

    monkeypatch.setattr(hub, "hf_hub_download", error_download)
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", SPEC])
    assert result.exit_code == 3, result.output
    assert result.stderr.count("error:") == 1
    assert message in result.stderr
    assert "untrusted upstream details" not in result.output


def test_huggingface_cli_invalid_tokenizer_file_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hub = pytest.importorskip("huggingface_hub")
    pytest.importorskip("tokenizers")
    bad_file = tmp_path / "tokenizer.json"
    bad_file.write_text("not JSON", encoding="utf-8")
    monkeypatch.setattr(hub, "hf_hub_download", lambda *a, **kw: str(bad_file))
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", SPEC])
    assert result.exit_code == 3, result.output
    assert "could not load tokenizer.json" in result.stderr


def test_huggingface_cli_missing_repo_is_a_usage_error() -> None:
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", "huggingface:"])
    assert result.exit_code == 2, result.output
    assert "needs a model repository" in result.stderr


def test_huggingface_counts_all_text_even_with_serialized_length_settings(
    local_tokenizer: list[str], tmp_path: Path
) -> None:
    tokenizers = pytest.importorskip("tokenizers")
    path = tmp_path / "tokenizer.json"
    tokenizer = tokenizers.Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=4)
    tokenizer.enable_padding(length=6, pad_token="[UNK]")
    tokenizer.save(str(path))

    counter = get_tokenizer(SPEC)
    assert counter.count_text("hello") == 1
    assert counter.count_text("hello world hello world hello world hello") == 7
    assert local_tokenizer == ["tokenizer.json"]


def test_huggingface_cli_transport_failure_exits_3_without_raw_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = pytest.importorskip("huggingface_hub")
    pytest.importorskip("tokenizers")
    major = int(hub.__version__.split(".", 1)[0])
    if major >= 2:
        transport_error = pytest.importorskip("httpx2").ProxyError
    elif major == 1:
        transport_error = pytest.importorskip("httpx").ProxyError
    else:
        transport_error = pytest.importorskip("requests").exceptions.ProxyError

    def fail_download(*args: Any, **kwargs: Any) -> str:
        raise transport_error("private proxy diagnostic")

    monkeypatch.setattr(hub, "hf_hub_download", fail_download)
    result = CliRunner().invoke(app, ["measure", SOURCE, "-t", SPEC])
    assert result.exit_code == 3, result.output
    assert result.stderr.count("error:") == 1
    assert "could not download tokenizer.json" in result.stderr
    assert "private proxy diagnostic" not in result.output
