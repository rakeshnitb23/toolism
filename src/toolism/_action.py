"""Entry point of the toolism GitHub Action (``action.yml``, design C13).

``action.yml`` passes its inputs as ``INPUT_*`` environment variables (never interpolated into a
shell script), and this module turns them into a ``toolism check`` run, prints the Markdown
summary to the log and writes the step outputs ``status``, ``total``, ``delta`` and
``summary-file``. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

__all__ = ["build_args", "main"]

_TRUE = {"1", "true", "yes", "on"}


class ActionInputError(Exception):
    """An invalid combination of action inputs."""


def _get(env: Mapping[str, str], name: str) -> str:
    return env.get("INPUT_" + name.upper().replace("-", "_"), "").strip()


def _flag(env: Mapping[str, str], name: str) -> bool:
    return _get(env, name).lower() in _TRUE


def build_args(env: Mapping[str, str], temp: Path) -> list[str]:
    """``toolism`` CLI arguments for the action inputs in ``env``."""
    source, mcp = _get(env, "source"), _get(env, "mcp")
    if bool(source) == bool(mcp):
        raise ActionInputError("set exactly one of the `source` and `mcp` inputs")
    args = ["check", *(["--mcp", mcp] if mcp else [source])]
    mcp_timeout = _get(env, "mcp-timeout")
    if mcp_timeout:
        args += ["--mcp-timeout", mcp_timeout]
    baseline = _get(env, "baseline")
    if baseline:
        args += ["--baseline", baseline]
    for value in _get(env, "max-increase").replace(",", " ").split():
        args += ["--max-increase", value]
    for name in ("max-total", "max-tool", "tokenizer"):
        value = _get(env, name)
        if value:
            args += [f"--{name}", value]
    if _flag(env, "update-baseline"):
        return [*args, "--update-baseline"]
    if _flag(env, "allow-tokenizer-change"):
        args.append("--allow-tokenizer-change")
    return [
        *args,
        "--format",
        "json",
        "--output",
        str(temp / "toolism.json"),
        "--summary-md",
        str(temp / "toolism.md"),
    ]


def _write_outputs(env: Mapping[str, str], outputs: Mapping[str, object]) -> None:
    target = env.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={'' if value is None else value}\n" for key, value in outputs.items())
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def main(env: Mapping[str, str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows runners pipe stdout as cp1252
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # the stream refuses: keep it
                reconfigure(encoding="utf-8")
    env = dict(os.environ if env is None else env)
    env.setdefault("PYTHONIOENCODING", "utf-8")  # for the toolism subprocess
    temp = Path(env.get("RUNNER_TEMP") or tempfile.gettempdir())
    temp.mkdir(parents=True, exist_ok=True)
    report, summary = temp / "toolism.json", temp / "toolism.md"
    for stale in (report, summary):
        stale.unlink(missing_ok=True)
    try:
        args = build_args(env, temp)
    except ActionInputError as exc:
        print(f"::error title=toolism::{exc}", flush=True)
        _write_outputs(env, {"status": "error", "total": None, "delta": None, "summary-file": None})
        return 2

    code = subprocess.call([sys.executable, "-m", "toolism", *args], env=env)
    status = {0: "pass", 1: "fail"}.get(code, "error")
    total = delta = None
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        total, delta = data.get("total_tokens"), data.get("delta_tokens")
    if summary.exists():
        print(summary.read_text(encoding="utf-8"), flush=True)
        if code == 1:
            print("::error title=toolism::tool definition token budget exceeded", flush=True)
    _write_outputs(
        env,
        {
            "status": status,
            "total": total,
            "delta": delta,
            "summary-file": summary if summary.exists() else None,
        },
    )
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
