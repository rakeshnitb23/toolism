"""The public API (architecture §9): everything in ``__all__`` resolves, lazily."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import toolism
from toolism.tokenizers import HeuristicTokenizer

FIXTURE = Path(__file__).parent / "fixtures" / "formats" / "mcp_tools.json"

ANTHROPIC = [
    {
        "name": "get_weather",
        "description": "Get the weather.",
        "input_schema": {
            "type": "object",
            "title": "get_weatherArguments",
            "properties": {"city": {"type": "string", "title": "City"}},
            "required": ["city"],
        },
    }
]


@pytest.mark.parametrize("name", toolism.__all__)
def test_every_public_name_resolves(name: str) -> None:
    assert getattr(toolism, name) is not None
    assert name in dir(toolism)


def test_no_submodule_shadows_a_public_name() -> None:
    """Importing ``toolism.x`` binds ``x`` on the package, hiding a lazy function ``x``."""
    package = Path(toolism.__file__).parent
    submodules = {p.stem for p in package.glob("*.py")} | {
        p.name for p in package.iterdir() if (p / "__init__.py").exists()
    }
    assert not submodules & set(toolism.__all__)
    importlib.import_module("toolism.measurement")
    importlib.import_module("toolism.pipeline")
    assert callable(toolism.measure)
    assert callable(toolism.slim)


def test_unknown_attribute() -> None:
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        _ = toolism.nope  # type: ignore[attr-defined]


def test_import_is_lazy() -> None:
    code = (
        "import json, sys, toolism; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('toolism'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    assert json.loads(out.stdout) == ["toolism", "toolism.__about__"]


def test_documented_workflow(tmp_path: Path) -> None:
    toolset = toolism.load(FIXTURE)
    report = toolism.measure(toolset, tokenizer="heuristic")
    assert report.total > 0
    result = toolism.slim(toolset, tokenizer="heuristic")
    assert result.report.after <= report.total
    out = tmp_path / "slim.json"
    toolism.dump(result.toolset, out)
    assert toolism.loads(out.read_text(encoding="utf-8")).format is toolism.ToolFormat.MCP
    assert [r.id for r in toolism.list_rules()][:2] == ["SL001", "SL002"]


def test_loads_accepts_a_utf8_bom() -> None:
    text = "\ufeff" + FIXTURE.read_text(encoding="utf-8")
    assert toolism.loads(text).format is toolism.ToolFormat.MCP


def test_slim_tools_keeps_shape_and_returns_fresh_objects() -> None:
    first: Any = toolism.slim_tools(ANTHROPIC)
    assert isinstance(first, list)
    assert first[0]["input_schema"] == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }
    first[0]["name"] = "mutated"
    second = toolism.slim_tools(ANTHROPIC)
    assert second[0]["name"] == "get_weather"  # cache is not affected by caller mutation
    assert ANTHROPIC[0]["input_schema"]["title"] == "get_weatherArguments"  # input untouched


def test_slim_tools_passes_options_and_handles_uncacheable_settings() -> None:
    kept = toolism.slim_tools(ANTHROPIC, disable=["SL001"])
    assert kept[0]["input_schema"]["title"] == "get_weatherArguments"
    uncached = toolism.slim_tools(ANTHROPIC, tokenizer=HeuristicTokenizer())
    assert uncached == toolism.slim_tools(ANTHROPIC)
