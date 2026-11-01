"""Read the servers of an MCP client config and load each one's tools (``measure --mcp-config``).

Claude Desktop, Cursor, Claude Code and others list their servers in the same shape::

    {"mcpServers": {"github": {"command": "npx", "args": ["-y", "..."], "env": {"TOKEN": "..."}}}}

Each stdio server is started with :func:`toolism.loaders.mcp.load_mcp_stdio`. Remote servers
(``url``) and entries marked ``"disabled": true`` are skipped, and one server failing does not
stop the others. ``env`` values are passed to the server and never appear in any message.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolism.errors import FormatError, LoaderError
from toolism.models import ToolSet

__all__ = ["ConfigServer", "load_mcp_config", "read_mcp_config"]

_REMOTE_TYPES = frozenset({"http", "sse", "streamable-http", "streamableHttp"})


@dataclass(frozen=True)
class ConfigServer:
    """One entry of ``mcpServers``. ``skip`` says why it will not be started, if it won't."""

    name: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    skip: str | None = None


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _server(name: str, entry: Any) -> ConfigServer:
    if not isinstance(entry, dict):
        return ConfigServer(name, skip="not a JSON object")
    if entry.get("disabled") is True:
        return ConfigServer(name, skip="disabled")
    command = entry.get("command")
    if command is None and ("url" in entry or entry.get("type") in _REMOTE_TYPES):
        return ConfigServer(name, skip="remote server (url); only stdio servers are supported")
    if not isinstance(command, str) or not command.strip():
        return ConfigServer(name, skip="no 'command' to start it with")
    args = _string_list(entry.get("args", []))
    if args is None:
        return ConfigServer(name, skip="'args' must be a list of strings")
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        return ConfigServer(name, skip="'env' must map names to strings")
    return ConfigServer(name, argv=(command, *args), env=env)


def read_mcp_config(path: str | os.PathLike[str]) -> tuple[ConfigServer, ...]:
    """Parse the ``mcpServers`` object of an MCP client config file, in file order."""
    path = Path(path)
    if path.is_dir():
        raise FormatError(f"expected a file but got a directory: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")  # -sig: Windows editors save UTF-8 BOMs
    except FileNotFoundError:
        raise FormatError(f"file not found: {path}") from None
    except UnicodeDecodeError:
        raise FormatError(f"{path} is not valid UTF-8") from None
    except OSError as exc:
        raise FormatError(f"cannot read {path}: {exc.strerror or exc}") from None
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FormatError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    if not isinstance(servers, dict):
        raise FormatError(
            f"no 'mcpServers' object found in {path}",
            hint="--mcp-config reads an MCP client config such as claude_desktop_config.json "
            "or .cursor/mcp.json",
        )
    if not servers:
        raise FormatError(f"'mcpServers' in {path} lists no servers")
    return tuple(_server(str(name), entry) for name, entry in servers.items())


def load_mcp_config(
    path: str | os.PathLike[str], *, timeout: float = 60.0
) -> tuple[list[tuple[str, ToolSet]], list[tuple[str, str]]]:
    """Start every stdio server in the config, one at a time, and list its tools.

    Returns ``(loaded, not_measured)``: ``(name, toolset)`` for each server that answered, and
    ``(name, reason)`` for each one that was skipped or failed. ``timeout`` applies per server.
    """
    from toolism.loaders.mcp import load_mcp_stdio

    servers = read_mcp_config(path)
    loaded: list[tuple[str, ToolSet]] = []
    not_measured: list[tuple[str, str]] = []
    for server in servers:
        if server.skip is not None:
            not_measured.append((server.name, f"skipped: {server.skip}"))
            continue
        try:
            loaded.append(
                (server.name, load_mcp_stdio(list(server.argv), env=server.env, timeout=timeout))
            )
        except (LoaderError, FormatError) as exc:  # a missing mcp extra still stops the run
            not_measured.append((server.name, exc.message))
    return loaded, not_measured
