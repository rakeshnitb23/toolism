"""The GitHub Action entry point (``toolism._action``) and ``action.yml`` wiring."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from toolism import _action

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tests" / "fixtures" / "formats" / "mcp_tools.json"


def env_for(tmp_path: Path, **inputs: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("INPUT_", "GITHUB_"))}
    env.update({"INPUT_" + k.upper(): v for k, v in inputs.items()})
    env["RUNNER_TEMP"] = str(tmp_path / "runner")
    env["GITHUB_OUTPUT"] = str(tmp_path / "output.txt")
    return env


def outputs(tmp_path: Path) -> dict[str, str]:
    text = (tmp_path / "output.txt").read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in text.splitlines())


def test_build_args(tmp_path: Path) -> None:
    env = {
        "INPUT_SOURCE": "tools.json",
        "INPUT_BASELINE": ".toolism/baseline.json",
        "INPUT_MAX_INCREASE": "10%, 500",
        "INPUT_MAX_TOOL": "800",
        "INPUT_TOKENIZER": "heuristic",
        "INPUT_ALLOW_TOKENIZER_CHANGE": "true",
    }
    assert _action.build_args(env, tmp_path) == [
        "check",
        "tools.json",
        "--baseline",
        ".toolism/baseline.json",
        "--max-increase",
        "10%",
        "--max-increase",
        "500",
        "--max-tool",
        "800",
        "--tokenizer",
        "heuristic",
        "--allow-tokenizer-change",
        "--format",
        "json",
        "--output",
        str(tmp_path / "toolism.json"),
        "--summary-md",
        str(tmp_path / "toolism.md"),
    ]


def test_build_args_mcp_timeout() -> None:
    env = {"INPUT_MCP": "npx -y server", "INPUT_MCP_TIMEOUT": "120"}
    args = _action.build_args(env, Path())
    assert args[:5] == ["check", "--mcp", "npx -y server", "--mcp-timeout", "120"]
    assert "--mcp-timeout" not in _action.build_args({"INPUT_MCP": "npx -y server"}, Path())


def test_build_args_mcp_and_update() -> None:
    env = {
        "INPUT_MCP": "npx -y server",
        "INPUT_BASELINE": "b.json",
        "INPUT_UPDATE_BASELINE": "TRUE",
    }
    assert _action.build_args(env, Path()) == [
        "check",
        "--mcp",
        "npx -y server",
        "--baseline",
        "b.json",
        "--update-baseline",
    ]


@pytest.mark.parametrize("inputs", [{}, {"INPUT_SOURCE": "a.json", "INPUT_MCP": "server"}])
def test_source_xor_mcp(inputs: dict[str, str]) -> None:
    with pytest.raises(_action.ActionInputError, match="exactly one"):
        _action.build_args(inputs, Path())


def test_bad_inputs_report_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _action.main(env_for(tmp_path)) == 2
    assert outputs(tmp_path)["status"] == "error"
    assert "::error title=toolism::set exactly one" in capsys.readouterr().out


def test_end_to_end_pass_then_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tools = tmp_path / "tools.json"
    tools.write_text(TOOLS.read_text(encoding="utf-8"), encoding="utf-8")
    baseline = str(tmp_path / "baseline.json")
    common = {"source": str(tools), "baseline": baseline, "tokenizer": "heuristic"}

    assert _action.main(env_for(tmp_path, **common, update_baseline="true")) == 0
    assert json.loads(Path(baseline).read_text(encoding="utf-8"))["tokenizer"] == "heuristic"
    (tmp_path / "output.txt").unlink()

    assert _action.main(env_for(tmp_path, **common)) == 0
    passed = outputs(tmp_path)
    assert passed["status"] == "pass"
    assert passed["delta"] == "0"
    assert int(passed["total"]) > 0
    assert passed["summary-file"].endswith("toolism.md")
    assert "### ✅ toolism" in capsys.readouterr().out
    (tmp_path / "output.txt").unlink()

    data = json.loads(tools.read_text(encoding="utf-8"))
    data["tools"][0]["description"] = "Create an issue in the tracker. " * 10
    tools.write_text(json.dumps(data), encoding="utf-8")
    assert _action.main(env_for(tmp_path, **common)) == 1
    failed = outputs(tmp_path)
    assert failed["status"] == "fail"
    assert int(failed["delta"]) > 0
    log = capsys.readouterr().out
    assert "### ❌ toolism" in log
    assert "::error title=toolism::tool definition token budget exceeded" in log


def test_outputs_go_to_stdout_without_github_output(capsys: pytest.CaptureFixture[str]) -> None:
    _action._write_outputs({}, {"status": "pass", "total": None})
    assert capsys.readouterr().out == "status=pass\ntotal=\n"


def test_action_yml_passes_every_input_through_env() -> None:
    """Every input reaches _action via INPUT_* env (never interpolated into the shell script)."""
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    inputs_block = text.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
    names = re.findall(r"^  ([a-z-]+):$", inputs_block, flags=re.M)
    assert "source" in names
    for name in names:
        if name == "python-version":
            continue
        env_name = "INPUT_" + name.upper().replace("-", "_")
        assert f"{env_name}: ${{{{ inputs.{name} }}}}" in text, name
    run_scripts = re.findall(r"run: \|\n((?:        .*\n?)+)", text)
    assert run_scripts
    assert all("inputs." not in script for script in run_scripts)


def test_readme_documents_every_action_input_and_output() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    inputs = action.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
    outputs_block = action.split("\noutputs:\n", 1)[1].split("\nruns:\n", 1)[0]
    input_names = re.findall(r"^  ([a-z-]+):$", inputs, flags=re.M)
    output_names = re.findall(r"^  ([a-z-]+):$", outputs_block, flags=re.M)
    input_table = readme.split("### Inputs\n", 1)[1].split("\n### Outputs\n", 1)[0]
    output_table = readme.split("### Outputs\n", 1)[1].split("\n```yaml", 1)[0]
    assert all("`" + name + "`" in input_table for name in input_names)
    assert all("`" + name + "`" in output_table for name in output_names)
