from __future__ import annotations

import json
import logging
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from servers.http_server import serve_http
from toolism import cli
from toolism.errors import FormatError, LoaderError, MissingExtraError
from toolism.formats import parse
from toolism.loaders import mcp_config
from toolism.models import ToolSet

DEMO = Path(__file__).parent / "servers" / "demo_server.py"


def _write(tmp_path: Path, document: Any, name: str = "mcp.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _toolset(*names: str) -> ToolSet:
    return parse({"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in names]})


# ------------------------------------------------------------------- reading


def test_read_stdio_servers_in_file_order(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "secret-value"},
                },
                "local": {"type": "stdio", "command": "python", "args": []},
                "bare": {"command": "uvx"},
            }
        },
    )
    servers = mcp_config.read_mcp_config(path)
    assert [s.name for s in servers] == ["github", "local", "bare"]
    assert servers[0].argv == ("npx", "-y", "@modelcontextprotocol/server-github")
    assert servers[0].env == {"GITHUB_TOKEN": "secret-value"}
    assert servers[2].argv == ("uvx",)
    assert all(s.skip is None for s in servers)
    assert "secret-value" not in repr(servers[0])


@pytest.mark.parametrize("kind", [None, "http", "streamable-http", "streamableHttp"])
def test_read_accepts_only_supported_http_forms(tmp_path: Path, kind: str | None) -> None:
    url = "http://127.0.0.1:1/mcp?token=synthetic-config-secret"
    entry = {"url": url} if kind is None else {"type": kind, "url": url}
    path = _write(tmp_path, {"mcpServers": {"remote": entry}})
    (server,) = mcp_config.read_mcp_config(path)
    assert server.skip is None
    assert server.url == url
    assert url not in repr(server)


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"command": "npx", "disabled": True}, "disabled"),
        ({"type": "sse"}, "legacy SSE"),
        ({"url": "http://127.0.0.1:1/mcp", "command": "npx"}, "both"),
        ({"type": "http"}, "'url'"),
        ({"type": "http", "command": "npx"}, "needs a non-empty 'url'"),
        ({"url": ""}, "'url'"),
        ({"url": "file:///tmp/not-http"}, "HTTP(S)"),
        ({"url": "http://synthetic-user:synthetic-pass@127.0.0.1:1/mcp"}, "credentials"),
        ({"type": "stdio", "url": "http://127.0.0.1:1/mcp"}, "transport"),
        ({"type": "unknown", "url": "http://127.0.0.1:1/mcp"}, "transport"),
        ({"url": "http://127.0.0.1:1/mcp", "headers": {}}, "#146"),
        ({"type": "http", "url": "http://127.0.0.1:1/mcp", "headers": None}, "#146"),
        ({"type": [], "command": "npx"}, "'type' must be a string"),
        ({"args": ["x"]}, "no 'command'"),
        ({"command": "  "}, "no 'command'"),
        ({"command": "npx", "args": "-y server"}, "'args' must be a list of strings"),
        ({"command": "npx", "env": {"PORT": 8080}}, "'env' must map names to strings"),
        ("npx -y server", "not a JSON object"),
    ],
)
def test_read_marks_entries_it_will_not_start(tmp_path: Path, entry: Any, reason: str) -> None:
    path = _write(tmp_path, {"mcpServers": {"s": entry}})
    (server,) = mcp_config.read_mcp_config(path)
    assert server.skip is not None
    assert reason in server.skip
    assert server.argv == ()


def test_read_accepts_a_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('﻿{"mcpServers": {"s": {"command": "x"}}}', encoding="utf-8")
    assert [s.name for s in mcp_config.read_mcp_config(path)] == ["s"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"tools": []}', "no 'mcpServers' object found"),
        ("[]", "no 'mcpServers' object found"),
        ('{"mcpServers": []}', "no 'mcpServers' object found"),
        ('{"mcpServers": {}}', "lists no servers"),
        ('{"mcpServers": ', "invalid JSON in"),
    ],
)
def test_read_rejects_files_that_are_not_configs(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(FormatError, match=message):
        mcp_config.read_mcp_config(path)


def test_read_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(FormatError, match="file not found"):
        mcp_config.read_mcp_config(tmp_path / "missing.json")
    with pytest.raises(FormatError, match="expected a file but got a directory"):
        mcp_config.read_mcp_config(tmp_path)


# ------------------------------------------------------------------- loading


def test_load_keeps_going_when_a_server_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "one": {"command": "one", "args": ["--flag"], "env": {"TOKEN": "secret-value"}},
                "broken": {"command": "broken"},
                "off": {"command": "off", "disabled": True},
                "two": {"command": "two"},
            }
        },
    )
    calls: list[tuple[list[str], dict[str, str], float]] = []

    def fake_load(command: list[str], *, env: Any = None, timeout: float = 60.0) -> ToolSet:
        calls.append((command, dict(env or {}), timeout))
        if command[0] == "broken":
            raise LoaderError(f"MCP server {shlex.join(command)!r} failed: command not found")
        return _toolset(f"{command[0]}_tool")

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_stdio", fake_load)
    loaded, not_measured = mcp_config.load_mcp_config(path, timeout=5)

    assert [(name, [t.name for t in ts.tools]) for name, ts in loaded] == [
        ("one", ["one_tool"]),
        ("two", ["two_tool"]),
    ]
    assert not_measured == [
        ("broken", "failed: command not found"),
        ("off", "skipped: disabled"),
    ]
    assert calls[0] == (["one", "--flag"], {"TOKEN": "secret-value"}, 5)
    assert [c[0][0] for c in calls] == ["one", "broken", "two"]  # disabled is never started
    assert "secret-value" not in repr(not_measured)


def test_failure_reasons_hide_the_command_line_and_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "db": {
                    "command": "npx",
                    "args": ["server-postgres", "postgresql://user:hunter2-arg@db/app"],
                    "env": {"API_KEY": "sk-secret-env-value", "PASSWORD": "hunter2", "DEBUG": "1"},
                }
            }
        },
    )

    def failing(command: list[str], *, env: Any = None, **_: Any) -> ToolSet:
        # Like the real loader: the command line in the message, plus a server error echoing a key.
        raise LoaderError(
            f"MCP server {shlex.join(command)!r} failed: "
            f"McpError: invalid key {env['API_KEY']} (debug={env['DEBUG']}); "
            f"cannot connect to {command[2]} with password {env['PASSWORD']}"
        )

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_stdio", failing)
    _, not_measured = mcp_config.load_mcp_config(path)
    assert not_measured == [
        (
            "db",
            "failed: McpError: invalid key *** (debug=1); cannot connect to *** with password ***",
        )
    ]


def test_load_stops_on_a_missing_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, {"mcpServers": {"a": {"command": "a"}, "b": {"command": "b"}}})
    started: list[str] = []

    def missing(command: list[str], **_: Any) -> ToolSet:
        started.append(command[0])
        raise MissingExtraError("Loading tools from an MCP server", "mcp")

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_stdio", missing)
    with pytest.raises(MissingExtraError):
        mcp_config.load_mcp_config(path)
    assert started == ["a"]


@pytest.mark.mcp
def test_load_live_servers_from_a_config(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "demo": {"command": sys.executable, "args": [str(DEMO)]},
                "missing": {"command": "toolism-no-such-command-xyz"},
            }
        },
    )
    loaded, not_measured = mcp_config.load_mcp_config(path, timeout=60)
    assert [name for name, _ in loaded] == ["demo"]
    assert [t.name for t in loaded[0][1].tools] == ["create_contact", "search_contacts"]
    assert [name for name, _ in not_measured] == ["missing"]
    assert not_measured[0][1].startswith("failed: command not found")


@pytest.mark.mcp
def test_measure_mixed_config_keeps_going_after_http_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mcp")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with (
        serve_http(mode="http_error") as broken,
        serve_http(sse=True) as good,
        serve_http() as skipped,
    ):
        # Neither the URL path nor an event-stream response implies legacy SSE.
        good_url = good.url.removesuffix("/mcp") + "/sse"
        path = _write(
            tmp_path,
            {
                "mcpServers": {
                    "broken": {"url": f"{broken.url}?token=synthetic-failure-secret"},
                    "local": {"command": sys.executable, "args": [str(DEMO)]},
                    "legacy": {"type": "sse", "url": skipped.url},
                    "private": {"url": skipped.url, "headers": {"X-Test": "synthetic-secret"}},
                    "working": {"url": good_url},
                }
            },
        )
        result = CliRunner().invoke(
            cli.app, ["measure", "--mcp-config", str(path), "-t", "heuristic", "-f", "json"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [s["name"] for s in data["sources"]] == ["local", "working"]
    assert data["tool_count"] == 4
    assert [row["name"] for row in data["not_measured"]] == ["broken", "legacy", "private"]
    assert data["not_measured"][0]["reason"].startswith("failed:")
    assert "legacy SSE" in data["not_measured"][1]["reason"]
    assert "#146" in data["not_measured"][2]["reason"]
    assert broken.requests
    assert good.terminated
    assert not skipped.requests
    assert "synthetic-secret" not in result.output
    assert "synthetic-failure-secret" not in result.output


@pytest.mark.mcp
def test_http_config_deadline_and_diagnostics_do_not_echo_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("TOOLISM_DEBUG", "1")
    caplog.set_level(logging.DEBUG, logger="mcp.client.streamable_http")
    caplog.set_level(logging.INFO, logger="httpx")
    caplog.set_level(logging.INFO, logger="httpx2")
    with serve_http(delay="tools/list") as endpoint:
        url = f"{endpoint.url}?token=synthetic-query-secret"
        path = _write(tmp_path, {"mcpServers": {"remote": {"url": url}}})
        loaded, rows = mcp_config.load_mcp_config(path, timeout=0.25)
    assert not loaded
    assert rows == [("remote", "failed: timed out after 0.25s")]
    assert any(r["method"] == "tools/list" for r in endpoint.requests)
    assert "synthetic-query-secret" not in repr(rows) + caplog.text


def test_http_config_propagates_missing_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, {"mcpServers": {"remote": {"url": "http://127.0.0.1:1/mcp"}}})

    def missing(url: str, **_: Any) -> ToolSet:
        raise MissingExtraError("Loading tools from an MCP server", "mcp")

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_http", missing)
    with pytest.raises(MissingExtraError):
        mcp_config.load_mcp_config(path)


@pytest.mark.parametrize(
    "logger_name",
    [
        "httpx",
        "httpx2",
        "httpcore.http11",
        "httpcore2.http11",
        "mcp.client.session",
        "mcp.shared.session",
    ],
)
def test_http_config_redacts_session_and_request_logs_only_during_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    logger_name: str,
) -> None:
    logger = logging.getLogger(logger_name)
    caplog.set_level(logging.WARNING, logger=logger_name)
    path = _write(tmp_path, {"mcpServers": {"remote": {"url": "http://127.0.0.1:1/mcp"}}})

    def fails(url: str, **_: Any) -> ToolSet:
        logger.warning("synthetic-remote-diagnostic")
        raise LoaderError("synthetic-remote-error")

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_http", fails)
    loaded, rows = mcp_config.load_mcp_config(path)
    logger.warning("unrelated diagnostic after discovery")
    assert not loaded
    assert rows == [("remote", "failed: streamable HTTP connection or tool discovery failed")]
    assert "synthetic-remote-diagnostic" not in caplog.text
    assert "unrelated diagnostic after discovery" in caplog.text
