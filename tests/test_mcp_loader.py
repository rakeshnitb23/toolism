from __future__ import annotations

import builtins
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest

from toolism.errors import LoaderError, MissingExtraError
from toolism.loaders import mcp as loader
from toolism.models import ToolFormat

DEMO = Path(__file__).parent / "servers" / "demo_server.py"


def _has_mcp() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


requires_mcp = pytest.mark.skipif(not _has_mcp(), reason="mcp extra not installed")


# ------------------------------------------------------------------ commands


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("npx -y server", ["npx", "-y", "server"]),
        ("python 'my server.py' --x=1", ["python", "my server.py", "--x=1"]),
        (["uvx", "mcp-server-git"], ["uvx", "mcp-server-git"]),
        (("a", Path("b")), ["a", "b"]),
    ],
)
def test_split_command(command: Any, argv: list[str]) -> None:
    assert loader.split_command(command) == argv


@pytest.mark.parametrize("command", ["", "   ", [], "python 'unterminated"])
def test_split_command_rejects(command: Any) -> None:
    with pytest.raises(LoaderError):
        loader.split_command(command)


# ---------------------------------------------------------------- pagination


@dataclass
class Page:
    tools: list[Any]
    nextCursor: str | None = None


@dataclass
class PageV2:
    tools: list[Any]
    next_cursor: str | None = None


@dataclass
class Params:
    cursor: str | None = None


class FakeTypes:
    PaginatedRequestParams = Params


@dataclass
class V2Session:
    pages: dict[str | None, Any]
    calls: list[str | None] = field(default_factory=list)

    async def list_tools(self, *, params: Params | None = None) -> Any:
        cursor = params.cursor if params else None
        self.calls.append(cursor)
        return self.pages[cursor]


@dataclass
class V1Session:
    pages: dict[str | None, Any]
    calls: list[str | None] = field(default_factory=list)

    async def list_tools(self, cursor: str | None = None) -> Any:
        self.calls.append(cursor)
        return self.pages[cursor]


@dataclass
class AncientSession:
    pages: dict[str | None, Any]

    async def list_tools(self) -> Any:
        return self.pages[None]


def run(session: Any, max_pages: int = loader.MAX_PAGES) -> list[Any]:
    async def main() -> list[Any]:
        return await loader._list_all_tools(session, FakeTypes, max_pages)

    return anyio.run(main)


def test_pagination_v2_params() -> None:
    session = V2Session({None: PageV2(["a"], "p2"), "p2": PageV2(["b"], "p3"), "p3": PageV2(["c"])})
    assert run(session) == ["a", "b", "c"]
    assert session.calls == [None, "p2", "p3"]


def test_pagination_v1_cursor() -> None:
    session = V1Session({None: Page(["a"], "next"), "next": Page(["b"], "")})
    assert run(session) == ["a", "b"]
    assert session.calls == [None, "next"]


def test_pagination_unsupported_stops_after_first_page() -> None:
    assert run(AncientSession({None: Page(["a"], "more")})) == ["a"]


def test_pagination_repeated_cursor() -> None:
    session = V2Session({None: PageV2(["a"], "x"), "x": PageV2(["b"], "x")})
    with pytest.raises(LoaderError, match="same tools/list cursor twice"):
        run(session)


def test_pagination_page_cap() -> None:
    pages = {None: PageV2(["t"], "0"), **{str(i): PageV2(["t"], str(i + 1)) for i in range(10)}}
    with pytest.raises(LoaderError, match="more than 3 pages"):
        run(V2Session(pages), max_pages=3)


# ------------------------------------------------------------------- helpers


def test_describe_unwraps_groups_and_names_errors() -> None:
    group = ExceptionGroupLike([ExceptionGroupLike([RuntimeError("boom")])])
    assert loader._describe(group, 5) == "RuntimeError: boom"
    assert loader._describe(TimeoutError(), 7.5) == "timed out after 7.5s"
    missing = FileNotFoundError(2, "No such file", "nope-cmd")
    assert loader._describe(missing, 1) == "command not found (nope-cmd)"
    assert loader._describe(ValueError(), 1) == "ValueError"


class ExceptionGroupLike(Exception):
    def __init__(self, exceptions: list[BaseException]) -> None:
        super().__init__("group")
        self.exceptions = exceptions


def test_tool_dict_accepts_plain_dicts() -> None:
    assert loader._tool_dict({"name": "x"}) == {"name": "x"}


def test_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MissingExtraError) as info:
        loader.load_mcp_stdio("anything")
    assert info.value.hint == "pip install 'toolism[mcp]'"


def test_broken_sdk_dependency_keeps_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("cannot import name 'BaseModel' from 'pydantic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(LoaderError) as info:
        loader.load_mcp_stdio("anything")
    assert not isinstance(info.value, MissingExtraError)
    assert "BaseModel" in info.value.message


# --------------------------------------------------------------- integration


@requires_mcp
@pytest.mark.mcp
def test_load_demo_server() -> None:
    from toolism.pipeline import slim

    toolset = loader.load_mcp_stdio([sys.executable, str(DEMO)], timeout=60)
    assert toolset.format is ToolFormat.MCP
    assert [t.name for t in toolset.tools] == ["create_contact", "search_contacts"]
    create = toolset.tools[0]
    assert create.description is not None
    assert create.description.startswith("Create a contact")
    assert create.input_schema["required"] == ["name"]
    assert "Address" in create.input_schema["$defs"]
    report = slim(toolset, tokenizer="heuristic").report
    assert report.saved > 0


@requires_mcp
@pytest.mark.mcp
def test_load_with_cwd_and_env(tmp_path: Path) -> None:
    script = tmp_path / "env_server.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            try:
                from mcp.server.mcpserver import MCPServer as Server
            except ImportError:
                from mcp.server.fastmcp import FastMCP as Server
            server = Server("env")
            name = os.environ["TOOL_NAME"] + "_" + os.path.basename(os.getcwd())

            @server.tool(name=name)
            def tool() -> str:
                \"\"\"Doc.\"\"\"
                return "ok"

            server.run()
            """
        ),
        encoding="utf-8",
    )
    toolset = loader.load_mcp_stdio(
        [sys.executable, "env_server.py"], cwd=tmp_path, env={"TOOL_NAME": "probe"}, timeout=60
    )
    assert [t.name for t in toolset.tools] == [f"probe_{tmp_path.name}"]


@requires_mcp
@pytest.mark.mcp
def test_command_not_found() -> None:
    with pytest.raises(LoaderError, match="failed: command not found"):
        loader.load_mcp_stdio("toolism-no-such-command-xyz", timeout=20)


@requires_mcp
@pytest.mark.mcp
def test_crashing_server_reports_stderr(tmp_path: Path) -> None:
    script = tmp_path / "crash.py"
    script.write_text(
        "import sys\nprint('kaboom: missing API key', file=sys.stderr)\nsys.exit(3)\n",
        encoding="utf-8",
    )
    with pytest.raises(LoaderError, match="failed") as info:
        loader.load_mcp_stdio([sys.executable, str(script)], timeout=20)
    assert info.value.hint is not None
    assert "kaboom: missing API key" in info.value.hint


@requires_mcp
@pytest.mark.mcp
def test_timeout(tmp_path: Path) -> None:
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    with pytest.raises(LoaderError, match="timed out after 1s"):
        loader.load_mcp_stdio([sys.executable, str(script)], timeout=1)


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        (
            '"C:\\Program Files\\Python\\python.exe" "C:\\srv\\my server.py" --x',
            ["C:\\Program Files\\Python\\python.exe", "C:\\srv\\my server.py", "--x"],
        ),
        ("npx -y @scope/server C:\\data", ["npx", "-y", "@scope/server", "C:\\data"]),
        ("cmd 'single quoted'", ["cmd", "single quoted"]),
    ],
)
def test_split_command_windows_rules(command: str, argv: list[str]) -> None:
    assert loader.split_command(command, posix=False) == argv
