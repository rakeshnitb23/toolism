"""Importing toolism must stay cheap: heavy optional dependencies load lazily (design C0)."""

from __future__ import annotations

import json
import subprocess
import sys

HEAVY = [
    "tiktoken",
    "hypothesis",
    "hypothesis_jsonschema",
    "jsonschema",
    "mcp",
    "anthropic",
    "tokenizers",
    "huggingface_hub",
]


def test_import_does_not_load_heavy_dependencies() -> None:
    code = (
        f"import json, sys, toolism; print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    assert json.loads(out) == []
