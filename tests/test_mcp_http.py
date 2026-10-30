from __future__ import annotations

import builtins
import contextlib
import importlib.util
import json
import logging
import os
import socket
import subprocess
import sys
from typing import Any

import anyio
import pytest
from typer.testing import CliRunner

import toolism
from servers.http_server import serve_http
from toolism import cli
from toolism.errors import LoaderError, MissingExtraError
from toolism.loaders import mcp as loader
from toolism.models import ToolFormat

pytestmark = [
    pytest.mark.mcp,
    pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp extra not installed"),
]


@pytest.mark.parametrize("sse", [False, True])
def test_http_loads_all_pages_and_preserves_schema_and_extras(sse: bool) -> None:
    with serve_http(sse=sse) as endpoint:
        tools = toolism.load_mcp_http(endpoint.url, timeout=5)
    assert tools.format is ToolFormat.MCP
    assert [tool.name for tool in tools.tools] == ["first", "second"]
    assert tools.tools[0].input_schema["required"] == ["city"]
    assert tools.tools[0].source["annotations"]["readOnlyHint"] is True
    assert endpoint.terminated
    assert [
        r.get("params", {}).get("cursor") for r in endpoint.requests if r["method"] == "tools/list"
    ] == [None, "page2"]


def test_http_async_and_empty_result() -> None:
    async def run() -> None:
        with serve_http(mode="empty") as endpoint:
            tools = await loader.load_mcp_http_async(endpoint.url, timeout=5)
        assert tools.tools == ()
        assert tools.format is ToolFormat.MCP

    anyio.run(run)


@pytest.mark.parametrize(
    ("mode", "message"), [("repeat", "same tools/list cursor"), ("endless", "more than 100 pages")]
)
def test_http_rejects_unbounded_pagination(mode: str, message: str) -> None:
    with serve_http(mode=mode) as endpoint, pytest.raises(LoaderError, match=message):
        loader.load_mcp_http(endpoint.url, timeout=10)


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_http_timeout_covers_initialization_and_listing(method: str) -> None:
    with (
        serve_http(delay=method) as endpoint,
        pytest.raises(LoaderError, match=r"timed out after 0\.1s") as info,
    ):
        loader.load_mcp_http(endpoint.url, timeout=0.1)
    assert info.value.hint


def test_http_connection_failure_has_hint() -> None:
    # Reserve a loopback port without listening so connection refusal is deterministic.
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        with pytest.raises(LoaderError, match=r"MCP HTTP server.*failed") as info:
            loader.load_mcp_http(f"http://127.0.0.1:{port.getsockname()[1]}/mcp", timeout=5)
    assert info.value.hint


@pytest.mark.parametrize("mode", ["http_error", "rpc_error"])
def test_http_cli_failures_are_one_error_line_with_hint(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TOOLISM_DEBUG", raising=False)
    with serve_http(mode=mode) as endpoint:
        result = CliRunner().invoke(
            cli.app, ["measure", "--mcp-url", endpoint.url, "-t", "heuristic"]
        )
    assert result.exit_code == 2
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 2
    assert result.stderr.startswith("error: MCP HTTP server")
    assert result.stderr.splitlines()[1].startswith("hint: ")
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["measure", "slim", "check"])
def test_http_cli_routes_through_real_loader(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with serve_http() as endpoint:
        args = [command, "--mcp-url", endpoint.url, "-t", "heuristic"]
        if command == "slim":
            args += ["--quiet"]
        else:
            args += ["-f", "json"]
        if command == "check":
            args += ["--max-total", "10000"]
        result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    if command == "slim":
        assert [t["name"] for t in data["tools"]] == ["first", "second"]
        assert "title" not in data["tools"][0]["inputSchema"]
    else:
        assert data["kind"] == command
    assert len([r for r in endpoint.requests if r["method"] == "tools/list"]) == 2


@pytest.mark.parametrize("command", ["measure", "slim", "check"])
@pytest.mark.parametrize("other", [["missing.json"], ["-"], ["--mcp", "do-not-start"]])
def test_http_cli_rejects_conflicting_inputs_before_connecting(
    command: str, other: list[str]
) -> None:
    with serve_http() as endpoint:
        args = [command, *other, "--mcp-url", endpoint.url]
        if command == "check":
            args += ["--max-total", "10000"]
        result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2
    assert "error: give only one of SOURCE, --mcp or --mcp-url" in result.stderr
    assert endpoint.requests == []


def test_http_missing_extra_fails_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingExtraError) as info:
        loader.load_mcp_http("http://127.0.0.1:1/mcp")
    assert "toolism[mcp]" in info.value.hint


def test_http_older_transport_name_and_three_item_yield(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.client import streamable_http

    original = getattr(streamable_http, "streamable_http_client", None)
    if original is None:
        original = streamable_http.streamablehttp_client

    @contextlib.asynccontextmanager
    async def older(url: str) -> Any:
        async with original(url) as streams:
            yield streams[0], streams[1], lambda: "session-id"

    monkeypatch.delattr(streamable_http, "streamable_http_client", raising=False)
    monkeypatch.setattr(streamable_http, "streamablehttp_client", older, raising=False)
    with serve_http() as endpoint:
        tools = loader.load_mcp_http(endpoint.url, timeout=5)
    assert [tool.name for tool in tools.tools] == ["first", "second"]


def test_http_missing_transport_has_upgrade_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.client import streamable_http

    monkeypatch.delattr(streamable_http, "streamable_http_client", raising=False)
    monkeypatch.delattr(streamable_http, "streamablehttp_client", raising=False)
    with pytest.raises(MissingExtraError) as info:
        loader.load_mcp_http("http://127.0.0.1:1/mcp")
    assert "--upgrade" in info.value.hint


@pytest.mark.parametrize("command", ["measure", "slim", "check"])
def test_http_cli_timeout_option(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOOLISM_DEBUG", raising=False)
    with serve_http(delay="tools/list") as endpoint:
        args = [command, "--mcp-url", endpoint.url, "--mcp-timeout", "0.1"]
        if command == "check":
            args += ["--max-total", "10000"]
        result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 2
    assert "timed out after 0.1s" in result.stderr
    assert result.stderr.splitlines()[1].startswith("hint: ")


@pytest.mark.parametrize("mode", ["html", "bad_json"])
@pytest.mark.parametrize("debug", [False, True])
def test_http_sdk_errors_in_real_cli_process(mode: str, debug: bool) -> None:
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("TOOLISM_DEBUG", None)
    if debug:
        env["TOOLISM_DEBUG"] = "1"
    with serve_http(mode=mode) as endpoint:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "toolism",
                "measure",
                "--mcp-url",
                endpoint.url,
                "--mcp-timeout",
                "2",
                "-t",
                "heuristic",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            env=env,
        )
    assert result.stdout == ""
    if debug:
        assert result.returncode != 0
        assert "Traceback" in result.stderr
        assert (
            "Unexpected content type" if mode == "html" else "Error parsing JSON response"
        ) in result.stderr
    else:
        assert result.returncode == 2
        assert len(result.stderr.splitlines()) == 2
        assert result.stderr.startswith("error: MCP HTTP server failed:")
        assert result.stderr.splitlines()[1].startswith("hint: ")


def test_http_log_filter_preserves_other_tasks_and_is_removed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("mcp.client.streamable_http")
    filters = list(logger.filters)
    caplog.set_level(logging.WARNING, logger=logger.name)

    async def run(url: str) -> None:
        async def load() -> None:
            with pytest.raises(LoaderError):
                await loader.load_mcp_http_async(url, timeout=2)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(load)
            # Wait until the loader is in its transport, before the delayed error response.
            with anyio.fail_after(2):
                while not endpoint.requests:
                    await anyio.sleep(0.01)
            logger.warning("unrelated task remains visible")

    with serve_http(mode="html", delay="initialize") as endpoint:
        anyio.run(run, endpoint.url)
    assert logger.filters == filters
    assert [record.message for record in caplog.records] == ["unrelated task remains visible"]
