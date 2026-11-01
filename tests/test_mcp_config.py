from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

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


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"command": "npx", "disabled": True}, "disabled"),
        ({"url": "https://example.com/mcp"}, "remote server"),
        ({"type": "http", "url": "https://example.com/mcp"}, "remote server"),
        ({"type": "sse"}, "remote server"),
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
            raise LoaderError("MCP server 'broken' failed: command not found (broken)")
        return _toolset(f"{command[0]}_tool")

    monkeypatch.setattr("toolism.loaders.mcp.load_mcp_stdio", fake_load)
    loaded, not_measured = mcp_config.load_mcp_config(path, timeout=5)

    assert [(name, [t.name for t in ts.tools]) for name, ts in loaded] == [
        ("one", ["one_tool"]),
        ("two", ["two_tool"]),
    ]
    assert not_measured == [
        ("broken", "MCP server 'broken' failed: command not found (broken)"),
        ("off", "skipped: disabled"),
    ]
    assert calls[0] == (["one", "--flag"], {"TOKEN": "secret-value"}, 5)
    assert [c[0][0] for c in calls] == ["one", "broken", "two"]  # disabled is never started
    assert "secret-value" not in repr(not_measured)


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
    assert "command not found" in not_measured[0][1]
