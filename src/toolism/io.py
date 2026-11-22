"""Read and write tool documents (design C2)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import IO, Any

from toolism.errors import FormatError
from toolism.formats import parse, serialize
from toolism.models import ToolSet

__all__ = ["dump", "dumps", "load", "loads"]

Source = str | os.PathLike[str] | IO[str]


def _reject_constant(name: str) -> Any:
    raise FormatError(f"invalid JSON value {name!r} (NaN and Infinity are not JSON)")


def loads(text: str) -> ToolSet:
    """Parse a JSON string into a :class:`ToolSet`."""
    if text.startswith("\ufeff"):  # Windows editors and PowerShell save UTF-8 BOMs
        text = text[1:]
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise FormatError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except RecursionError as exc:
        # Extremely deep JSON can exhaust the stack inside the decoder itself,
        # before parse() gets a chance to apply its depth limit (#138).
        raise FormatError(
            "input is nested too deeply",
            hint="flatten the schema or split it into smaller tools",
        ) from exc
    try:
        return parse(document)
    except RecursionError as exc:
        # Backstop for any recursive path the depth check does not cover:
        # a stack exhaustion on input is an input error, not a bug report.
        raise FormatError(
            "input is nested too deeply",
            hint="flatten the schema or split it into smaller tools",
        ) from exc


def _read(source: Source) -> str:
    if isinstance(source, str) and source == "-":
        return sys.stdin.read()
    if not isinstance(source, (str, os.PathLike)):
        return source.read()
    path = Path(source)
    if path.is_dir():  # Windows raises PermissionError, not IsADirectoryError, for directories
        raise FormatError(f"expected a file but got a directory: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FormatError(
            f"file not found: {path}",
            hint="check the path, or pass '-' to read from stdin (or --mcp to read from a server)",
        ) from exc
    except UnicodeDecodeError as exc:
        raise FormatError(f"{path} is not valid UTF-8") from exc
    except OSError as exc:
        raise FormatError(f"cannot read {path}: {exc.strerror or exc}") from exc


def load(source: Source) -> ToolSet:
    """Load a :class:`ToolSet` from a path, an open text file, or ``"-"`` for stdin."""
    return loads(_read(source))


def dumps(toolset: ToolSet, *, indent: int | None = 2) -> str:
    """Serialize a :class:`ToolSet` back to JSON text (same format and envelope as the input)."""
    document = serialize(toolset)
    if indent is None:
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(document, ensure_ascii=False, indent=indent)
    return text + "\n"


def dump(toolset: ToolSet, target: Source, *, indent: int | None = 2) -> None:
    """Write a :class:`ToolSet` to a path, an open text file, or ``"-"`` for stdout."""
    text = dumps(toolset, indent=indent)
    if isinstance(target, str) and target == "-":
        sys.stdout.write(text)
    elif isinstance(target, (str, os.PathLike)):
        Path(target).write_text(text, encoding="utf-8")
    else:
        target.write(text)
