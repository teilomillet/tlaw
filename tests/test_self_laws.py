"""Independent checks that tlaw's self-laws accept, reject, and replay honestly."""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import tlaw
from tlaw.audit import KnownWrongOutcome
from tlaw.replay import decode_value, encode_value


ROOT = Path(__file__).resolve().parents[1]
SELF_LAWS = ROOT / "selflaws"
LAW_FILES = (
    "replay_laws.py",
    "comparison_laws.py",
    "domains_laws.py",
    "paths_laws.py",
)
# Independent of source discovery and the audit declaration: a lost law fails CI.
EXPECTED_LAWS = {
    "replay_laws.py::round_trip_preserves_scalar_value_and_type",
    "replay_laws.py::round_trip_preserves_container_structure",
    "replay_laws.py::round_trip_preserves_sets",
    "replay_laws.py::integer_wire_format_is_explicit",
    "replay_laws.py::integer_wire_decoding_is_explicit",
    "comparison_laws.py::comparison_matches_independent_numeric_reference",
    "comparison_laws.py::booleans_remain_distinct_from_integers",
    "comparison_laws.py::a_changed_nested_leaf_names_its_location",
    "domains_laws.py::integer_generation_respects_declared_bounds",
    "domains_laws.py::list_generation_respects_declared_size",
    "domains_laws.py::dependent_values_fit_their_budget",
    "paths_laws.py::composition_runs_steps_in_declared_order",
    "paths_laws.py::identity_and_grouping_preserve_results",
    "paths_laws.py::paths_start_fresh_on_every_execution",
}
CHALLENGES = (
    (
        "loses-integer-sign",
        "LosesIntegerSign",
        "replay_laws.py::round_trip_preserves_scalar_value_and_type",
    ),
    (
        "collapses-tuples",
        "CollapsesTuples",
        "replay_laws.py::round_trip_preserves_container_structure",
    ),
    (
        "shifted-wire-integers",
        "ShiftedWireIntegers",
        "replay_laws.py::integer_wire_format_is_explicit",
    ),
    (
        "always-equal",
        "AlwaysEqual",
        "comparison_laws.py::comparison_matches_independent_numeric_reference",
    ),
    (
        "ignores-tolerance",
        "IgnoresTolerance",
        "comparison_laws.py::comparison_matches_independent_numeric_reference",
    ),
    (
        "widens-integer-domain",
        "WidensIntegerDomain",
        "domains_laws.py::integer_generation_respects_declared_bounds",
    ),
    (
        "permits-empty-lists",
        "PermitsEmptyLists",
        "domains_laws.py::list_generation_respects_declared_size",
    ),
    (
        "shifts-dependency",
        "ShiftsDependency",
        "domains_laws.py::dependent_values_fit_their_budget",
    ),
    (
        "drops-composed-step",
        "DropsComposedStep",
        "paths_laws.py::composition_runs_steps_in_declared_order",
    ),
    (
        "reuses-state",
        "ReusesState",
        "paths_laws.py::paths_start_fresh_on_every_execution",
    ),
)


def _environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("TLAW_")
        and name not in {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    return environment


def _run_laws(
    implementation: str,
    receipt: Path,
    *,
    directory: Path = SELF_LAWS,
) -> subprocess.CompletedProcess[str]:
    environment = _environment()
    environment["TLAW_TARGET"] = f"implementations:{implementation}"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-o",
            "addopts=",
            f"--junitxml={receipt}",
            *LAW_FILES,
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _assert_rejection(
    completed: subprocess.CompletedProcess[str], receipt: Path, expected: str
) -> None:
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, (
        f"known-wrong implementation survived or did not execute:\n{output}"
    )
    report = ET.parse(receipt).getroot()
    assert _law_names(report) == EXPECTED_LAWS, output
    assert not report.findall(".//error"), output
    assert not report.findall(".//skipped"), output
    filename, _, law_name = expected.partition("::")
    [case] = [
        case
        for case in report.findall(".//testcase")
        if case.get("name") == law_name
        and case.get("classname", "").split(".")[-1] == Path(filename).stem
    ]
    failure = case.find("failure")
    assert failure is not None, f"expected law did not fail: {expected}\n{output}"
    assert failure.get("message", "").startswith("AssertionError: law failed: "), (
        f"expected a law assertion in {expected}:\n{output}"
    )


def _law_names(report: ET.Element) -> set[str]:
    return {
        f"{case.get('classname', '').split('.')[-1]}.py::{case.get('name')}"
        for case in report.findall(".//testcase")
    }


def test_real_implementation_passes_the_self_laws(tmp_path: Path) -> None:
    receipt = tmp_path / "baseline.xml"
    completed = _run_laws("Core", receipt)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = ET.parse(receipt).getroot()
    assert _law_names(report) == EXPECTED_LAWS
    for outcome in ("failure", "error", "skipped"):
        assert not report.findall(f".//{outcome}"), completed.stdout


@pytest.mark.parametrize(
    ("name", "implementation", "expected"),
    CHALLENGES,
    ids=[name for name, _, _ in CHALLENGES],
)
def test_known_wrong_implementation_fails_its_named_law(
    tmp_path: Path, name: str, implementation: str, expected: str
) -> None:
    receipt = tmp_path / f"{name}.xml"
    completed = _run_laws(implementation, receipt)

    _assert_rejection(completed, receipt, expected)


def test_audit_rejects_each_declared_challenge() -> None:
    result = tlaw.audit(SELF_LAWS / "audit.py")

    assert result.baseline_valid is True, result.baseline.output
    assert result.total_known_wrong == len(CHALLENGES)
    assert result.rejected_count == len(CHALLENGES)
    assert result.sufficient is True
    for name, _, expected in CHALLENGES:
        assert result.outcome_for(name) is KnownWrongOutcome.REJECTED
        [run] = [run for run in result.known_wrong if run.name == name]
        assert expected in run.failed_tests, run.output


@pytest.mark.parametrize(
    ("value", "encoded"),
    [
        (None, {"type": "none"}),
        (True, {"type": "bool", "value": True}),
        (7, {"type": "int", "value": "7"}),
        (-7, {"type": "int", "value": "-7"}),
        (-0.0, {"type": "float", "value": "-0.0"}),
        (math.inf, {"type": "float", "value": "inf"}),
        (-math.inf, {"type": "float", "value": "-inf"}),
        (math.nan, {"type": "float", "value": "nan"}),
        (b"\x00\xff", {"type": "bytes", "base64": "AP8="}),
        ((7,), {"type": "tuple", "items": [{"type": "int", "value": "7"}]}),
    ],
)
def test_replay_wire_fixtures_have_independent_literal_expectations(
    value: object, encoded: dict[str, object]
) -> None:
    assert encode_value(value) == encoded
    decoded = decode_value(encoded)
    assert type(decoded) is type(value)
    if isinstance(value, float) and math.isnan(value):
        assert math.isnan(decoded)
    else:
        assert decoded == value
    if isinstance(value, float) and value == 0:
        assert math.copysign(1, decoded) == math.copysign(1, value)


def _copy_suite(destination: Path) -> Path:
    return Path(
        shutil.copytree(
            SELF_LAWS,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
    )


def test_self_law_counterexample_replays_and_detects_the_repair(
    tmp_path: Path,
) -> None:
    suite = _copy_suite(tmp_path / "selflaws")
    law_file = suite / "replay_laws.py"
    original = law_file.read_text(encoding="utf-8")
    assert "implementations:Core" in original
    law_file.write_text(
        original.replace("implementations:Core", "implementations:LosesIntegerSign"),
        encoding="utf-8",
    )

    result = tlaw.check(law_file, search=True, seed=20261023)

    assert result.exit_code == 1, result.output
    failures = result.report["failures"]
    assert any(
        failure["identity"].endswith("::round_trip_preserves_scalar_value_and_type")
        and failure["inputs"] is not None
        and failure["exception"]["type"] == "builtins.AssertionError"
        for failure in failures
    )
    retained = tmp_path / "counterexamples.json"
    retained.write_text(result.to_json(), encoding="utf-8")

    reproduced = tlaw.replay(retained)
    assert reproduced.exit_code == 0, reproduced.output
    assert "retained counterexample" in reproduced.output
    assert "reproduced" in reproduced.output

    law_file.write_text(original, encoding="utf-8")
    repaired = tlaw.replay(retained)
    assert repaired.exit_code == 1, repaired.output
    assert "no longer fails" in repaired.output


def test_independent_guard_does_not_count_a_type_error_as_a_law_rejection(
    tmp_path: Path,
) -> None:
    suite = _copy_suite(tmp_path / "selflaws")
    configuration = suite / "conftest.py"
    configuration.write_text(
        configuration.read_text(encoding="utf-8")
        + "\nfrom implementations import LosesIntegerSign\n\n"
        + "def broken_encoder(value):\n"
        + "    raise TypeError('broken encoder')\n\n"
        + "LosesIntegerSign.encode = staticmethod(broken_encoder)\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "wrong-exception.xml"
    completed = _run_laws("LosesIntegerSign", receipt, directory=suite)

    with pytest.raises(AssertionError, match="expected a law assertion"):
        _assert_rejection(
            completed,
            receipt,
            "replay_laws.py::round_trip_preserves_scalar_value_and_type",
        )


def test_independent_guard_rejects_an_all_accepting_law_checker(
    tmp_path: Path,
) -> None:
    suite = _copy_suite(tmp_path / "selflaws")
    (suite / "conftest.py").write_text(
        """\
import tlaw


def accept_every_law(function=None, **strategies):
    def decorate(function):
        def passed():
            pass
        passed.__test__ = True
        return passed
    return decorate(function) if callable(function) else decorate


tlaw.law = accept_every_law
""",
        encoding="utf-8",
    )
    driver = tmp_path / "test_independent_guard.py"
    driver.write_text(
        "from pathlib import Path\n"
        "import runpy\n\n"
        f"helpers = runpy.run_path({str(Path(__file__).resolve())!r})\n\n"
        "def test_wrong_implementation_must_be_rejected():\n"
        f"    receipt = Path({str(tmp_path / 'sabotage.xml')!r})\n"
        "    completed = helpers['_run_laws'](\n"
        "        'LosesIntegerSign', receipt,\n"
        f"        directory=Path({str(suite)!r}),\n"
        "    )\n"
        "    helpers['_assert_rejection'](\n"
        "        completed, receipt,\n"
        "        'replay_laws.py::round_trip_preserves_scalar_value_and_type',\n"
        "    )\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", str(driver)],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "known-wrong implementation survived" in completed.stdout
    assert "FAILED test_independent_guard.py" in completed.stdout
