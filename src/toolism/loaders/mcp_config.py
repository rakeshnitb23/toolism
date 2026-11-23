"""Read the servers of an MCP client config and load each one's tools (``measure --mcp-config``).

Claude Desktop, Cursor, Claude Code and others list their servers in the same shape::

    {"mcpServers": {"github": {"command": "npx", "args": ["-y", "..."], "env": {"TOKEN": "..."}}}}

Stdio and headerless streamable-HTTP servers are measured in file order. Disabled entries,
legacy SSE and remote entries with headers are skipped. One server failing does not stop the
others. Stdio errors mask command arguments and environment values; HTTP errors omit endpoint
URLs and remote error details.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from toolism.errors import FormatError, LoaderError
from toolism.models import ToolSet

__all__ = ["ConfigServer", "load_mcp_config", "read_mcp_config"]

_HTTP_TYPES = frozenset({"http", "streamable-http", "streamableHttp"})
_MIN_MASKED = 3  # "1" or "-y" would mask ordinary text; any real secret is longer


@dataclass(frozen=True)
class ConfigServer:
    """One entry of ``mcpServers``. ``skip`` says why it will not be started, if it won't."""

    name: str
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    skip: str | None = None
    url: str | None = field(default=None, repr=False)


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _http_server(name: str, entry: dict[str, Any], kind: str | None) -> ConfigServer:
    if "headers" in entry:
        return ConfigServer(name, skip="HTTP headers are not supported (see #146)")
    if kind is not None and kind not in _HTTP_TYPES:
        return ConfigServer(name, skip="unsupported transport for a 'url' entry")
    if "command" in entry and "url" in entry:
        return ConfigServer(name, skip="entry cannot specify both 'command' and 'url'")
    url = entry.get("url")
    if not isinstance(url, str) or not url.strip():
        return ConfigServer(name, skip="streamable HTTP needs a non-empty 'url' string")
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {"http", "https"} and bool(parts.hostname)
        # Reading port validates its syntax and range.
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        return ConfigServer(name, skip="'url' must be an HTTP(S) URL with a host")
    if parts.username is not None or parts.password is not None:
        return ConfigServer(name, skip="HTTP URL credentials are not supported (see #146)")
    return ConfigServer(name, url=url)


def _server(name: str, entry: Any) -> ConfigServer:
    if not isinstance(entry, dict):
        return ConfigServer(name, skip="not a JSON object")
    if entry.get("disabled") is True:
        return ConfigServer(name, skip="disabled")
    kind = entry.get("type")
    if "type" in entry and not isinstance(kind, str):
        return ConfigServer(name, skip="'type' must be a string")
    if kind == "sse":
        return ConfigServer(name, skip="legacy SSE transport is not supported; use streamable HTTP")
    if "url" in entry or kind in _HTTP_TYPES:
        return _http_server(name, entry, kind)
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return ConfigServer(name, skip="no 'command' to start it with")
    args = _string_list(entry.get("args", []))
    if args is None:
        return ConfigServer(name, skip="'args' must be a list of strings")
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        return ConfigServer(name, skip="'env' must map names to strings")
    return ConfigServer(name, argv=(command, *args), env=env)


def _reason(exc: LoaderError | FormatError, server: ConfigServer) -> str:
    """The error without the server's command line, with its ``env`` and argument values masked.

    The row already names the server. The rest of the text can come from the server itself,
    which may echo a value back (a rejected API key, a connection string), so every value of
    ``_MIN_MASKED`` or more characters is masked wherever it appears, longest first.
    """
    text = exc.message.removeprefix(f"MCP server {shlex.join(server.argv)!r} ")
    values = {*server.env.values(), *server.argv[1:]}
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= _MIN_MASKED:
            text = text.replace(value, "***")
    return text


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
    """List each configured server's tools, one server at a time.

    Returns ``(loaded, not_measured)``: ``(name, toolset)`` for each server that answered, and
    ``(name, reason)`` for each one that was skipped or failed. ``timeout`` applies per server.
    """
    from toolism.loaders.mcp import _load_config_http, load_mcp_stdio

    servers = read_mcp_config(path)
    loaded: list[tuple[str, ToolSet]] = []
    not_measured: list[tuple[str, str]] = []
    for server in servers:
        if server.skip is not None:
            not_measured.append((server.name, f"skipped: {server.skip}"))
            continue
        try:
            if server.url is None:
                tools = load_mcp_stdio(list(server.argv), env=server.env, timeout=timeout)
            else:
                tools = _load_config_http(server.url, timeout=timeout)
            loaded.append((server.name, tools))
        except (LoaderError, FormatError) as exc:  # a missing mcp extra still stops the run
            reason = _reason(exc, server) if server.url is None else exc.message
            not_measured.append((server.name, reason))
    return loaded, not_measured
