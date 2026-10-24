"""SL010 boundary cases with instances that distinguish an unsafe merge."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from toolism import slim_tools
from toolism.models import ToolDef
from toolism.rules import get_rule


@pytest.mark.parametrize(
    ("left", "right", "instance", "draft"),
    [
        ({"properties": {"x": {}}}, {"additionalProperties": False}, {"x": 1}, "2020-12"),
        ({"patternProperties": {"^x": {}}}, {"additionalProperties": False}, {"x": 1}, "2020-12"),
        ({"prefixItems": [{}]}, {"items": False}, [1], "2020-12"),
        ({"items": [{}]}, {"additionalItems": False}, [1, 2], "draft-07"),
        ({"contains": {"const": 1}}, {"minContains": 2}, [1], "2020-12"),
        ({"contains": {"const": 1}}, {"maxContains": 1}, [1, 1], "2020-12"),
        ({"if": {"type": "string"}}, {"then": {"minLength": 2}}, "a", "2020-12"),
        ({"if": {"type": "string"}}, {"else": {"minimum": 1}}, 0, "2020-12"),
        ({"minimum": 1}, {"exclusiveMinimum": True}, 1, "draft-04"),
        ({"maximum": 1}, {"exclusiveMaximum": True}, 1, "draft-04"),
        ({"properties": {"x": {}}}, {"unevaluatedProperties": False}, {"x": 1}, "2020-12"),
        ({"prefixItems": [{}]}, {"unevaluatedItems": False}, [1], "2020-12"),
    ],
)
def test_sibling_interactions_have_real_counterexamples(
    left: dict[str, Any], right: dict[str, Any], instance: Any, draft: str
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    uri = (
        f"http://json-schema.org/{draft}/schema#"
        if draft.startswith("draft-")
        else f"https://json-schema.org/draft/{draft}/schema"
    )
    for parent, branch in ((left, right), (right, left)):
        original = {"$schema": uri, **parent, "allOf": [branch]}
        naive = {"$schema": uri, **parent, **branch}
        validator = jsonschema.validators.validator_for(original)
        before, after = validator(original).is_valid(instance), validator(naive).is_valid(instance)
        if {"unevaluatedProperties", "unevaluatedItems"} & parent.keys():
            # The parent can see annotations from allOf, but the branch cannot
            # see its parent's annotations. Both orientations are kept unchanged.
            assert before == after
        else:
            assert before != after, "the instance must expose the unsafe merge"
        tool = ToolDef(name="t", input_schema=original)
        assert get_rule("SL010")().apply((tool,), {})[0] is tool


def test_non_plain_branch_is_unchanged() -> None:
    class SchemaDict(dict[str, Any]):
        pass

    tool = ToolDef(name="t", input_schema={"allOf": [SchemaDict(type="string")]})
    assert get_rule("SL010")().apply((tool,), {})[0] is tool


def test_draft7_ref_must_not_swallow_parent_constraints() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    original = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {"X": {"type": "string"}},
        "minLength": 2,
        "allOf": [{"$ref": "#/definitions/X"}],
    }
    naive = {key: value for key, value in original.items() if key != "allOf"}
    naive["$ref"] = "#/definitions/X"
    assert not jsonschema.Draft7Validator(original).is_valid("a")
    assert jsonschema.Draft7Validator(naive).is_valid("a")
    tool = ToolDef(name="t", input_schema=original)
    assert get_rule("SL010")().apply((tool,), {})[0] is tool


def test_installed_cli_and_public_api(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"allOf": [{"type": "string", "minLength": 1}]},
            "closed": {"properties": {"x": {}}, "allOf": [{"additionalProperties": False}]},
        },
    }
    document = [{"name": "example", "input_schema": schema}]
    pristine = copy.deepcopy(document)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    command = [sys.executable, "-m", "toolism", "slim", str(source), "-t", "heuristic", "-q"]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    output = json.loads(result.stdout)
    assert output[0]["input_schema"]["properties"]["name"] == {"type": "string", "minLength": 1}
    assert output[0]["input_schema"]["properties"]["closed"] == schema["properties"]["closed"]
    assert slim_tools(document, tokenizer="heuristic") == output
    assert document == pristine
    assert json.loads(source.read_text(encoding="utf-8")) == pristine
    assert slim_tools(output, tokenizer="heuristic") == output
    disabled = subprocess.run(
        [*command, "--disable", "SL010"], check=True, text=True, capture_output=True
    )
    assert json.loads(disabled.stdout) == pristine

    pytest.importorskip("hypothesis_jsonschema")
    verified = subprocess.run(
        [*command, "--verify", "--fail-on-rollback"], check=True, text=True, capture_output=True
    )
    assert json.loads(verified.stdout) == output
