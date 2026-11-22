"""Enforce the architecture's layering rule (docs/architecture/ARCHITECTURE.md §4, review R11).

Every internal import in ``src/toolism`` must go to a group that the importing group is allowed
to depend on. Lower layers never import higher ones, and rules never import the pipeline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "toolism"

# group -> groups it may import (besides itself)
ALLOWED: dict[str, set[str]] = {
    "__about__": set(),
    "errors": set(),
    "models": {"errors"},
    "schema": {"errors"},
    "formats": {"errors", "models"},
    "io": {"errors", "models", "formats"},
    "tokenizers": {"errors", "models", "__about__"},
    "loaders": {"errors", "models", "formats"},
    "rules": {"errors", "models", "schema"},
    "verify": {"errors", "schema"},
    "measurement": {"errors", "models", "tokenizers"},
    "pipeline": {"errors", "models", "schema", "tokenizers", "rules", "verify"},
    "report": {"errors", "models", "measurement", "pipeline", "rules", "verify"},
    "check": {"errors", "models", "measurement", "__about__"},
    # top layer: may import anything
    "cli": {"*"},
    "__main__": {"cli"},
    "_action": set(),  # GitHub Action glue: stdlib only, runs the CLI in a subprocess
    "__init__": {"*"},
}


def _group_of(module: str) -> str:
    # module like "toolism.rules.sl001_redundant_title" -> "rules"
    parts = module.split(".")
    if len(parts) == 1:
        return "__init__"
    return parts[1]


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _internal_imports(path: Path) -> list[str]:
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.split(".")[0] == "toolism")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            if target.split(".")[0] == "toolism":
                found.append(target)
    return found


MODULES = sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(SRC)))
def test_module_respects_layers(path: Path) -> None:
    group = _group_of(_module_name(path))
    assert group in ALLOWED, f"{path} belongs to unknown group {group!r}; add it to ALLOWED"
    allowed = ALLOWED[group]
    if "*" in allowed:
        return
    for target in _internal_imports(path):
        target_group = _group_of(target)
        if target_group == group:
            continue
        assert target_group in allowed, (
            f"{_module_name(path)} ({group}) imports {target} ({target_group}); "
            f"allowed: {sorted(allowed)}"
        )
