"""Cycle contracts retain stable evidence even when recursion fails at varying sites."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import tlaw


ROOT = Path(__file__).resolve().parents[1]

# The two exceptions deliberately have different origins and messages. A
# termination law must reject both with the same replayable law assertion.
BROKEN = """\
calls = 0


def fails_on_cycles(*args, **kwargs):
    global calls
    calls += 1
    if calls % 2:
        raise RecursionError("first recursion site")
    raise RecursionError("another recursion site")


same = fails_on_cycles
excludes = fails_on_cycles
"""
REPAIRED = """\
from tlaw.comparison import same
from tlaw.exclusion import excludes
"""


@pytest.mark.parametrize(
    ("filename", "name"),
    [
        ("comparison_laws.py", "cycles_use_structural_value_semantics"),
        ("comparison_laws.py", "mapping_cycles_use_the_same_structural_semantics"),
        ("exclusion_laws.py", "cycles_are_traversed_once_without_hiding_later_leaks"),
        ("exclusion_laws.py", "dataclass_cycles_are_safe_and_searchable"),
    ],
)
def test_cycle_refutations_are_retained_replayed_and_rejected_after_repair(
    tmp_path: Path, filename: str, name: str
) -> None:
    source = (ROOT / "self_laws" / filename).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    declaration = ast.get_source_segment(source, function)
    laws = tmp_path / "laws.py"
    laws.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import tlaw\n"
        "from implementation import same, excludes\n"
        "SmallInt = tlaw.integer(-100, 100)\n"
        "SECRET = 'db-prod.internal'\n\n"
        f"@tlaw.law\n{declaration}\n",
        encoding="utf-8",
    )
    implementation = tmp_path / "implementation.py"
    implementation.write_text(BROKEN, encoding="utf-8")

    checked = tlaw.check(laws)

    assert checked.exit_code == 1
    assert "FlakyFailure" not in checked.output
    recorded = json.loads(checked.to_json())
    assert recorded["summary"]["status"] == "failed"
    assert len(recorded["failures"]) == 1
    failure = recorded["failures"][0]
    assert failure["name"] == name
    assert failure["capture_error"] is None
    assert failure["inputs"]["value"]["type"] == "int"
    assert failure["exception"]["type"] == "builtins.AssertionError"
    report = tmp_path / "failure.json"
    report.write_text(checked.to_json(), encoding="utf-8")

    replayed = tlaw.replay(report)

    assert replayed.exit_code == 0
    assert replayed.report["obligations"][0]["method"] == "replay"
    assert replayed.report["obligations"][0]["cases_checked"] == 1

    implementation.write_text(REPAIRED, encoding="utf-8")

    refused = tlaw.replay(report)

    assert refused.exit_code == 1
    assert "retained counterexample no longer fails" in refused.output
