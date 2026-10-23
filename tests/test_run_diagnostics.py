"""Independent child-process challenges for diagnostic-only execution evidence."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
import tlaw

import tlaw.mutation as mutation
from tlaw.diagnostics import read_diagnostics
from tlaw.mutation import MutationOutcome, mutate


IMPLEMENTATION = "def value():\n    return 1\n"
PASSING_LAW = """
import tlaw

@tlaw.law
def identity_holds(flag: bool):
    return True
"""
VALUE_LAW = """
import tlaw
from implementation import value

@tlaw.law
def observes_value(flag: bool):
    return value() == 1
"""


def _law(directory: Path, source: str) -> Path:
    path = directory / "laws.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _boundary(directory: Path, source: str = VALUE_LAW) -> tuple[Path, Path]:
    implementation = directory / "implementation.py"
    implementation.write_text(IMPLEMENTATION, encoding="utf-8")
    return _law(directory, source), implementation


def _diagnostic(
    diagnostics: list[dict[str, Any]], *, phase: str, exception: str
) -> dict[str, Any]:
    [diagnostic] = [
        item
        for item in diagnostics
        if item["phase"] == phase and item["exception_type"] == exception
    ]
    assert set(diagnostic) == {
        "nodeid",
        "identity",
        "phase",
        "exception_type",
        "message",
        "detail",
    }
    return diagnostic


@pytest.mark.parametrize("flaky", [False, True], ids=["deadline", "flaky"])
def test_hypothesis_failures_outside_the_law_body_explain_invalid_runs(
    tmp_path: Path, flaky: bool
) -> None:
    # The body always passes. Hypothesis imposes the deadline outside that
    # body, so there is no law-body counterexample to retain or replay.
    sleeping_body = (
        "global calls\n    calls += 1\n    if calls == 1:\n        time.sleep(0.05)"
        if flaky
        else "time.sleep(0.05)"
    )
    laws = _law(
        tmp_path,
        "import time\n"
        "from hypothesis import settings, strategies as st\n"
        "import tlaw\n"
        "calls = 0\n"
        "@settings(deadline=1, database=None, derandomize=True)\n"
        '@tlaw.law("timed body", flag=st.just(None))\n'
        "def timed_body(flag):\n    " + sleeping_body + "\n    return True\n",
    )

    result = tlaw.check(laws)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    exception = "FlakyFailure" if flaky else "DeadlineExceeded"
    diagnostic = _diagnostic(
        result.report["diagnostics"],
        phase="call",
        exception=f"hypothesis.errors.{exception}",
    )
    assert diagnostic["identity"] == result.report["obligations"][0]["identity"]
    assert diagnostic["nodeid"] == "laws.py::timed_body"
    assert "DeadlineExceeded" in diagnostic["detail"]
    assert "1.00ms" in diagnostic["detail"]
    assert json.loads(result.to_json())["diagnostics"] == result.report["diagnostics"]


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_failures_retain_the_phase_without_refuting_the_law(
    tmp_path: Path, phase: str
) -> None:
    fixture = (
        'raise RuntimeError("fixture setup unavailable")\n    yield'
        if phase == "setup"
        else 'yield\n    raise RuntimeError("fixture teardown unavailable")'
    )
    laws = _law(
        tmp_path,
        "import pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def broken_fixture():\n    " + fixture + "\n" + PASSING_LAW,
    )

    result = tlaw.check(laws)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    diagnostic = _diagnostic(
        result.report["diagnostics"], phase=phase, exception="builtins.RuntimeError"
    )
    assert f"fixture {phase} unavailable" in diagnostic["message"]
    assert diagnostic["identity"] == result.report["obligations"][0]["identity"]


def test_collection_failure_retains_the_pytest_wrapper_and_import_cause(
    tmp_path: Path,
) -> None:
    laws = _law(tmp_path, "import missing_module_for_diagnostic_regression\n")

    result = tlaw.check(laws)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == result.report["obligations"] == []
    diagnostic = _diagnostic(
        result.report["diagnostics"],
        phase="collect",
        exception="_pytest.nodes.Collector.CollectError",
    )
    assert diagnostic["identity"] is None
    assert "missing_module_for_diagnostic_regression" in diagnostic["message"]
    assert "ModuleNotFoundError" in diagnostic["detail"]


def test_a_passing_run_has_no_diagnostics(tmp_path: Path) -> None:
    result = tlaw.check(_law(tmp_path, PASSING_LAW))

    assert result.passed
    assert result.report["diagnostics"] == []


def test_process_timeout_reports_the_runner_exception_without_a_counterexample(
    tmp_path: Path,
) -> None:
    laws = _law(
        tmp_path,
        "import time\nimport tlaw\n@tlaw.law\ndef slow_law(flag: bool):\n"
        "    time.sleep(30)\n    return True\n",
    )

    result = tlaw.check(laws, timeout_seconds=0.1)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    diagnostic = _diagnostic(
        result.report["diagnostics"],
        phase="run",
        exception="subprocess.TimeoutExpired",
    )
    assert "0.1" in diagnostic["message"]


@pytest.mark.parametrize("marker", ["skip", "xfail"])
def test_skipped_laws_keep_the_explanation_without_becoming_valid_evidence(
    tmp_path: Path, marker: str
) -> None:
    arguments = 'reason="declared incomplete execution"'
    if marker == "xfail":
        arguments += ", run=False"
    laws = _law(
        tmp_path,
        "import pytest\nimport tlaw\n"
        f"@pytest.mark.{marker}({arguments})\n"
        "@tlaw.law\ndef incomplete_law(flag: bool):\n    return False\n",
    )

    result = tlaw.check(laws)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert any(
        "declared incomplete execution" in item["message"]
        or "declared incomplete execution" in item["detail"]
        for item in result.report["diagnostics"]
    )


def test_chained_exception_details_preserve_both_causes(tmp_path: Path) -> None:
    laws = _law(
        tmp_path,
        "import tlaw\n@tlaw.law\ndef chained_failure(flag: bool):\n"
        "    try:\n        raise ValueError('underlying resource unavailable')\n"
        "    except ValueError as error:\n"
        "        raise RuntimeError('cannot execute comparison') from error\n",
    )

    result = tlaw.check(laws)

    assert result.exit_code == 1
    diagnostic = _diagnostic(
        result.report["diagnostics"], phase="call", exception="builtins.RuntimeError"
    )
    assert diagnostic["message"] == "cannot execute comparison"
    assert "underlying resource unavailable" in diagnostic["detail"]
    assert "ValueError" in diagnostic["detail"]
    assert result.report["failures"], "diagnostics must not erase real counterexamples"


def test_replay_replaces_the_original_failure_diagnostic(tmp_path: Path) -> None:
    laws = _law(
        tmp_path,
        "import tlaw\n@tlaw.law\ndef stable_failure(flag: bool):\n    return False\n",
    )
    first = tlaw.check(laws)
    assert first.exit_code == 1
    assert first.report["diagnostics"]
    report = tmp_path / "counterexample.json"
    report.write_text(first.to_json(), encoding="utf-8")

    replayed = tlaw.replay(report)

    assert replayed.passed
    assert replayed.report["diagnostics"] == []
    laws.write_text(laws.read_text().replace("return False", "return True"))
    repaired = tlaw.replay(report)
    assert not repaired.passed
    assert repaired.report["diagnostics"]
    assert "no longer fails" in repaired.output


def test_invalid_mutation_baseline_keeps_diagnostics_and_runs_no_challenges(
    tmp_path: Path,
) -> None:
    laws, implementation = _boundary(
        tmp_path, "raise RuntimeError('baseline cannot collect')\n"
    )

    result = mutate(laws, implementation)

    assert not result.baseline_valid
    assert result.canary is None
    assert result.runs_started == 1
    diagnostic = _diagnostic(
        result.to_dict()["baseline"]["diagnostics"],
        phase="collect",
        exception="builtins.RuntimeError",
    )
    assert diagnostic["message"] == "baseline cannot collect"
    assert all(run.outcome is MutationOutcome.NOT_RUN for run in result.runs)


def test_invalid_canary_keeps_its_own_diagnostics_and_cannot_establish_wiring(
    tmp_path: Path,
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def canary_fixture():\n"
        "    if 'tlaw canary' in Path('implementation.py').read_text():\n"
        "        raise RuntimeError('canary setup unavailable')\n" + VALUE_LAW,
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None
    assert result.canary.wired is None
    assert result.runs_started == 2
    payload = result.to_dict()
    assert payload["baseline"]["diagnostics"] == []
    diagnostic = _diagnostic(
        payload["canary"]["diagnostics"],
        phase="setup",
        exception="builtins.RuntimeError",
    )
    assert diagnostic["message"] == "canary setup unavailable"
    assert result.killed_count == 0
    assert all(run.outcome is MutationOutcome.NOT_RUN for run in result.runs)


def test_ordinary_neighbor_failure_is_diagnostic_and_never_a_mutant_kill(
    tmp_path: Path,
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        "from pathlib import Path\nimport tlaw\nfrom implementation import value\n"
        "@tlaw.law\ndef observes_value(flag: bool):\n    return value() in (1, 2)\n"
        "def test_neighbor():\n"
        "    if 'return 2' in Path('implementation.py').read_text():\n"
        "        raise RuntimeError('neighbor unavailable under mutation')\n",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired
    [run] = result.runs
    assert run.outcome is MutationOutcome.ERROR
    assert run.kill_kind is None
    assert run.failed_tests == ()
    assert run.replay_method is None
    assert result.killed_count == 0
    diagnostic = _diagnostic(
        run.evidence["diagnostics"], phase="call", exception="builtins.RuntimeError"
    )
    assert diagnostic["identity"] is None
    assert diagnostic["nodeid"] == "laws.py::test_neighbor"
    assert diagnostic["message"] == "neighbor unavailable under mutation"


def test_confirmation_failure_keeps_candidate_and_replay_diagnostics_apart(
    tmp_path: Path,
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        "import os\nimport pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def replay_fixture():\n"
        "    if os.environ.get('TLAW_REPLAY'):\n"
        "        raise RuntimeError('confirmation setup unavailable')\n" + VALUE_LAW,
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired
    [run] = result.runs
    assert run.outcome is MutationOutcome.ERROR
    assert run.kill_kind is None
    assert run.failed_tests == ()
    assert run.replay_method == "exact"
    assert run.evidence["failures"]
    _diagnostic(
        run.evidence["diagnostics"], phase="call", exception="builtins.AssertionError"
    )
    replay = _diagnostic(
        run.replay_evidence["diagnostics"],
        phase="setup",
        exception="builtins.RuntimeError",
    )
    assert replay["message"] == "confirmation setup unavailable"
    assert result.to_dict()["mutations"][0]["replay_evidence"] == run.replay_evidence
    assert result.killed_count == 0


def test_observed_kill_still_requires_successful_exact_replay(tmp_path: Path) -> None:
    laws, implementation = _boundary(tmp_path)

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired
    [run] = result.runs
    assert run.outcome is MutationOutcome.KILLED
    assert run.replay_method == "exact"
    assert run.failed_tests == ("laws.py::observes_value",)
    assert run.evidence["diagnostics"]
    assert run.replay_evidence["diagnostics"] == []
    assert result.to_dict()["canary"]["diagnostics"]


def test_failed_timeout_calibration_keeps_fresh_baseline_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(tmp_path)
    original = mutation.subprocess.run
    phases: list[str] = []

    def child(command, **kwargs):
        directory = Path(kwargs["cwd"])
        source = (directory / "implementation.py").read_text()
        if "tlaw canary" in source:
            phases.append("canary")
        elif "return 2" in source:
            phases.append("mutant")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        else:
            phases.append("baseline")
            if phases.count("baseline") == 2:
                with (directory / "laws.py").open("a") as target:
                    target.write(
                        "\nraise RuntimeError('fresh calibration unavailable')\n"
                    )
        return original(command, **kwargs)

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation)

    assert phases == ["baseline", "canary", "mutant", "baseline"]
    assert result.baseline_valid
    [run] = result.runs
    assert run.outcome is MutationOutcome.ERROR
    assert run.kill_kind is None
    assert run.failed_tests == ()
    assert run.replay_method is None
    diagnostic = _diagnostic(
        run.timeout_baseline_evidence["diagnostics"],
        phase="collect",
        exception="builtins.RuntimeError",
    )
    assert diagnostic["message"] == "fresh calibration unavailable"
    assert result.to_dict()["mutations"][0]["timeout_baseline_evidence"] == (
        run.timeout_baseline_evidence
    )
    assert result.killed_count == 0


@pytest.mark.parametrize("receipt", [None, "not JSON", "{}"])
def test_broken_diagnostic_receipt_explains_its_own_failure_without_changing_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt: str | None
) -> None:
    laws = _law(tmp_path, PASSING_LAW)
    original = mutation.subprocess.run

    def child(command, **kwargs):
        completed = original(command, **kwargs)
        destination = Path(kwargs["env"]["TLAW_DIAGNOSTICS"])
        if receipt is None:
            destination.unlink()
        else:
            destination.write_text(receipt, encoding="utf-8")
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = tlaw.check(laws)

    assert result.passed
    assert result.report["summary"]["status"] == "passed"
    assert result.report["failures"] == []
    assert any(item["phase"] == "receipt" for item in result.report["diagnostics"])


@pytest.mark.parametrize("operation", ["check", "mutate"])
def test_broken_obligation_receipt_does_not_discard_the_actual_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    laws, implementation = _boundary(
        tmp_path, "raise RuntimeError('actual collection failure')\n"
    )
    original = mutation.subprocess.run

    def child(command, **kwargs):
        completed = original(command, **kwargs)
        Path(kwargs["env"]["TLAW_OBLIGATIONS"]).write_text("not JSON")
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    if operation == "check":
        checked = tlaw.check(laws)
        assert checked.exit_code == 2
        diagnostics = checked.report["diagnostics"]
    else:
        challenged = mutate(laws, implementation)
        assert not challenged.baseline_valid
        diagnostics = challenged.to_dict()["baseline"]["diagnostics"]

    diagnostic = _diagnostic(
        diagnostics, phase="collect", exception="builtins.RuntimeError"
    )
    assert diagnostic["message"] == "actual collection failure"


def test_inherited_diagnostic_destination_is_not_written_by_check_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign_receipt = tmp_path / "foreign.json"
    foreign_receipt.write_text("parent-owned", encoding="utf-8")
    monkeypatch.setenv("TLAW_DIAGNOSTICS", str(foreign_receipt))
    laws, implementation = _boundary(tmp_path)

    checked = tlaw.check(laws)
    challenged = mutate(laws, implementation)

    assert checked.passed
    assert challenged.killed_count == 1
    assert foreign_receipt.read_text() == "parent-owned"


def test_forged_diagnostics_cannot_turn_a_surviving_mutant_into_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path, VALUE_LAW.replace("value() == 1", "value() in (1, 2)")
    )
    original = mutation.subprocess.run
    forged = {
        "nodeid": "laws.py::observes_value",
        "identity": "laws.py::observes_value",
        "phase": "call",
        "exception_type": "builtins.AssertionError",
        "message": "diagnostic claims a failure without execution evidence",
        "detail": "forged diagnostic only",
    }

    def child(command, **kwargs):
        completed = original(command, **kwargs)
        Path(kwargs["env"]["TLAW_DIAGNOSTICS"]).write_text(json.dumps([forged]))
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired
    [run] = result.runs
    assert run.outcome is MutationOutcome.SURVIVED
    assert run.kill_kind is None
    assert run.failed_tests == ()
    assert run.evidence["diagnostics"][0]["message"] == forged["message"]
    assert result.killed_count == 0


@pytest.mark.parametrize("operation", ["check", "mutate"])
def test_diagnostic_write_failure_cannot_prevent_obligation_evidence(
    tmp_path: Path, operation: str
) -> None:
    laws, implementation = _boundary(tmp_path)
    (tmp_path / "conftest.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_sessionfinish(session):\n"
        "    Path(os.environ['TLAW_DIAGNOSTICS']).mkdir()\n",
        encoding="utf-8",
    )

    if operation == "check":
        result = tlaw.check(laws)
        assert result.passed
        assert result.report["summary"]["status"] == "passed"
        assert result.report["obligations"][0]["status"] == "passed"
        diagnostics = [result.report["diagnostics"]]
    else:
        challenged = mutate(laws, implementation)
        assert challenged.baseline_valid
        assert challenged.canary is not None and challenged.canary.wired
        [run] = challenged.runs
        assert run.outcome is MutationOutcome.KILLED
        assert run.replay_method == "exact"
        assert run.replay_exit_code == 0
        assert run.failed_tests == ("laws.py::observes_value",)
        assert run.evidence["failures"]
        payload = challenged.to_dict()
        diagnostics = [
            payload["baseline"]["diagnostics"],
            payload["canary"]["diagnostics"],
            run.evidence["diagnostics"],
            run.replay_evidence["diagnostics"],
        ]

    for entries in diagnostics:
        _diagnostic(entries, phase="receipt", exception="builtins.IsADirectoryError")


def test_deeply_nested_diagnostic_json_reports_a_receipt_error(tmp_path: Path) -> None:
    receipt = tmp_path / "diagnostics.json"
    receipt.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")

    diagnostics = read_diagnostics(receipt)

    _diagnostic(
        [entry.to_dict() for entry in diagnostics],
        phase="receipt",
        exception="builtins.RecursionError",
    )
