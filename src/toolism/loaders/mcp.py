"""Load the tools an MCP server advertises over stdio or streamable HTTP (design C12).

Works with the ``mcp`` SDK 1.x and 2.x: pagination is feature-detected (``list_tools()`` with
no argument, ``cursor=`` or ``params=``), as is the result's ``nextCursor``/``next_cursor``.
"""

from __future__ import annotations

import inspect
import logging
import os
import shlex
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import replace
from typing import IO, Any

from toolism.errors import LoaderError, MissingExtraError
from toolism.formats import parse
from toolism.models import ToolFormat, ToolSet

__all__ = [
    "MAX_PAGES",
    "load_mcp_http",
    "load_mcp_http_async",
    "load_mcp_stdio",
    "load_mcp_stdio_async",
    "split_command",
]

MAX_PAGES = 100
_STDERR_TAIL = 15
_QUIET_HTTP_LOGS: ContextVar[bool] = ContextVar("toolism_quiet_mcp_http", default=False)


class _HttpLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Transport tasks inherit this context; unrelated calls on this logger stay visible.
        return not _QUIET_HTTP_LOGS.get()


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def split_command(command: str | Sequence[str], *, posix: bool | None = None) -> list[str]:
    """``"npx -y server --flag 'a b'"`` → argv. Sequences are taken as argv already.

    Strings are split with POSIX rules, or Windows-friendly rules (backslashes are not
    escapes) on Windows; ``posix`` overrides the platform default.
    """
    if isinstance(command, str):
        posix = os.name != "nt" if posix is None else posix
        try:
            argv = shlex.split(command, posix=posix)
        except ValueError as exc:
            raise LoaderError(f"cannot parse MCP command {command!r}: {exc}") from None
        if not posix:  # non-POSIX mode keeps the quotes: '"C:\\Program Files\\x.exe"'
            argv = [_unquote(part) for part in argv]
    else:
        argv = [str(part) for part in command]
    if not argv:
        raise LoaderError("the MCP server command is empty")
    return argv


def _import_mcp() -> tuple[Any, Any, Any, Any]:
    try:
        from mcp import ClientSession, StdioServerParameters, types
        from mcp.client.stdio import stdio_client
    except ImportError:
        raise MissingExtraError("Loading tools from an MCP server", "mcp") from None
    return ClientSession, StdioServerParameters, stdio_client, types


def _next_cursor(result: Any) -> str | None:
    cursor = getattr(result, "next_cursor", None) or getattr(result, "nextCursor", None)
    return str(cursor) if cursor else None


async def _list_all_tools(session: Any, types: Any, max_pages: int = MAX_PAGES) -> list[Any]:
    """Page through ``tools/list`` until the server stops returning a cursor."""
    parameters = inspect.signature(session.list_tools).parameters
    tools: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        if cursor is None:
            result = await session.list_tools()
        elif "params" in parameters:
            result = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
        elif "cursor" in parameters:
            result = await session.list_tools(cursor=cursor)
        else:  # very old SDK without pagination support
            break
        tools.extend(result.tools)
        cursor = _next_cursor(result)
        if cursor is None:
            return tools
        if cursor in seen:
            raise LoaderError(f"the server returned the same tools/list cursor twice ({cursor!r})")
        seen.add(cursor)
    else:
        raise LoaderError(f"the server returned more than {max_pages} pages of tools")
    return tools


def _tool_dict(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        return tool
    dumped: dict[str, Any] = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dumped


def _leaf(exc: BaseException) -> BaseException:
    """The first real error inside (possibly nested) exception groups."""
    while True:  # duck-typed: BaseExceptionGroup is 3.11+, anyio uses a backport on 3.10
        inner = getattr(exc, "exceptions", None)
        if not isinstance(inner, (list, tuple)) or not inner:
            return exc
        exc = inner[0]


def _describe(exc: BaseException, timeout: float) -> str:
    leaf = _leaf(exc)
    if isinstance(leaf, TimeoutError):
        return f"timed out after {timeout:g}s"
    if isinstance(leaf, FileNotFoundError):
        return f"command not found ({leaf.filename or leaf})"
    text = str(leaf).strip()
    return f"{type(leaf).__name__}: {text}" if text else type(leaf).__name__


def _stderr_tail(log: IO[str] | None) -> str | None:
    if log is None or log is sys.stderr:
        return None
    try:
        log.flush()
        log.seek(0)
        lines = [line.rstrip() for line in log.read().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    if not lines:
        return None
    return "server stderr (last lines):\n  " + "\n  ".join(lines[-_STDERR_TAIL:])


async def load_mcp_stdio_async(
    command: str | Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 60.0,
) -> ToolSet:
    """Async variant of :func:`load_mcp_stdio`, for callers already inside an event loop."""
    import anyio

    client_session, server_parameters, stdio_client, types = _import_mcp()
    argv = split_command(command)
    params = server_parameters(
        command=argv[0],
        args=argv[1:],
        env={**os.environ, **(env or {})},
        cwd=os.fspath(cwd) if cwd is not None else None,
    )
    debug = bool(os.environ.get("TOOLISM_DEBUG"))
    log: IO[str] = (
        sys.stderr
        if debug
        else tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")  # noqa: SIM115
    )
    client_kwargs = (
        {"errlog": log} if "errlog" in inspect.signature(stdio_client).parameters else {}
    )
    try:
        with anyio.fail_after(timeout):
            async with stdio_client(params, **client_kwargs) as (read, write):  # noqa: SIM117
                async with client_session(read, write) as session:
                    await session.initialize()
                    raw = await _list_all_tools(session, types)
    except LoaderError:
        raise
    except Exception as exc:
        raise LoaderError(
            f"MCP server {shlex.join(argv)!r} failed: {_describe(exc, timeout)}",
            hint=_stderr_tail(log) or "set TOOLISM_DEBUG=1 to see the server's stderr",
        ) from exc
    finally:
        if log is not sys.stderr:
            log.close()
    return parse({"tools": [_tool_dict(tool) for tool in raw]})


def load_mcp_stdio(
    command: str | Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 60.0,
) -> ToolSet:
    """Start an MCP server over stdio, list its tools and return them as an MCP-format ToolSet.

    ``command`` is a shell-like string (split with :func:`shlex.split`) or an argv list. The
    server inherits this process's environment, overlaid with ``env``.
    """
    _import_mcp()  # fail fast with the install hint, before touching the event loop
    import anyio

    async def run() -> ToolSet:
        return await load_mcp_stdio_async(command, env=env, cwd=cwd, timeout=timeout)

    return anyio.run(run)


def _import_mcp_http() -> tuple[Any, Any, Any]:
    try:
        from mcp import ClientSession, types
        from mcp.client import streamable_http

        transport = getattr(streamable_http, "streamable_http_client", None)
        if transport is None:  # older SDK 1.x spelling
            transport = getattr(streamable_http, "streamablehttp_client", None)
        if transport is None:
            raise ImportError("MCP SDK has no streamable-HTTP client")
    except ImportError:
        error = MissingExtraError("Loading tools from a streamable-HTTP MCP server", "mcp")
        error.hint = "pip install --upgrade 'toolism[mcp]' (an SDK with streamable HTTP is needed)"
        raise error from None
    return ClientSession, transport, types


async def load_mcp_http_async(url: str, *, timeout: float = 60.0) -> ToolSet:
    """Async variant of :func:`load_mcp_http`, for callers already inside an event loop."""
    import anyio

    client_session, transport, types = _import_mcp_http()
    logger = logging.getLogger("mcp.client.streamable_http")
    log_filter = _HttpLogFilter()
    token = _QUIET_HTTP_LOGS.set(not bool(os.environ.get("TOOLISM_DEBUG")))
    logger.addFilter(log_filter)
    try:
        with anyio.fail_after(timeout):
            async with transport(url) as streams:  # noqa: SIM117
                # SDK 1.x also yields a session-id callback; 2.x yields just the streams.
                async with client_session(streams[0], streams[1]) as session:
                    await session.initialize()
                    raw = await _list_all_tools(session, types)
    except Exception as exc:
        leaf = _leaf(exc)
        detail = leaf.message if isinstance(leaf, LoaderError) else _describe(exc, timeout)
        raise LoaderError(
            f"MCP HTTP server failed: {' '.join(detail.splitlines())}",
            hint=(
                "check the streamable-HTTP endpoint URL and connectivity, or increase --mcp-timeout"
            ),
        ) from exc
    finally:
        logger.removeFilter(log_filter)
        _QUIET_HTTP_LOGS.reset(token)
    # An empty tools/list is still MCP; automatic format detection has no item to inspect.
    return replace(parse({"tools": [_tool_dict(tool) for tool in raw]}), format=ToolFormat.MCP)


def load_mcp_http(url: str, *, timeout: float = 60.0) -> ToolSet:
    """Connect to a streamable-HTTP MCP endpoint and return all its tools.

    ``timeout`` bounds connection, initialization and pagination together. Requires the
    ``mcp`` extra with a streamable-HTTP-capable SDK. No tool is executed.
    """
    _import_mcp_http()  # fail fast with the install hint, before touching the event loop
    import anyio

    async def run() -> ToolSet:
        return await load_mcp_http_async(url, timeout=timeout)

    return anyio.run(run)
