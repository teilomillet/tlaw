"""Challenge diagnostic receipts independently of pytest and the law runner."""

from copy import deepcopy

import pytest

from scripts.check_installed import verify_invalid_check, verify_invalid_mutation


@pytest.fixture
def invalid_check() -> dict:
    identity = "diagnostic_laws.py::still_passes"
    return {
        "schema": "tlaw.check/v1",
        "summary": {"status": "invalid", "failures": 0, "retained": True},
        "failures": [],
        "obligations": [{"identity": identity, "status": "error"}],
        "diagnostics": [
            {
                "nodeid": identity,
                "identity": identity,
                "phase": "setup",
                "exception_type": "builtins.RuntimeError",
                "message": "fixture setup failed",
                "detail": (
                    "ValueError: fixture source rejected\n"
                    "The above exception was the direct cause of the following exception:\n"
                    "RuntimeError: fixture setup failed"
                ),
            }
        ],
    }


@pytest.fixture
def invalid_mutation(invalid_check: dict) -> dict:
    return {
        "schema": "tlaw.mutation/v1",
        "baseline": {
            "valid": False,
            "exit_code": 1,
            "timed_out": False,
            "obligations": deepcopy(invalid_check["obligations"]),
            "diagnostics": deepcopy(invalid_check["diagnostics"]),
        },
        "canary": None,
        "work": {"runs_started": 1},
        "summary": {
            "generated": 1,
            "selected": 1,
            "excluded": 0,
            "not_run": 1,
            "runnable": 0,
            "killed": 0,
            "survived": 0,
            "errors": 0,
            "complete": False,
            "selection_complete": False,
            "has_errors": True,
        },
        "mutations": [
            {
                "outcome": "not-run",
                "exit_code": None,
                "timed_out": False,
                "duration_seconds": 0.0,
                "kill_kind": None,
                "failed_tests": [],
                "evidence": {},
                "replay_evidence": {},
                "timeout_baseline_evidence": {},
                "replay_method": None,
                "replay_exit_code": None,
                "replay_timed_out": None,
                "replay_duration_seconds": None,
            }
        ],
    }


def test_invalid_check_with_chained_setup_exception_is_accepted(
    invalid_check: dict,
) -> None:
    verify_invalid_check(invalid_check)


def test_invalid_baseline_with_chained_setup_exception_is_accepted(
    invalid_mutation: dict,
) -> None:
    verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize("stage", ["check", "mutation"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nodeid", "other_laws.py::still_passes"),
        ("identity", None),
        ("phase", "call"),
        ("exception_type", "builtins.AssertionError"),
        ("message", "assert False"),
        ("detail", "RuntimeError: fixture setup failed"),
        ("detail", "ValueError: fixture source rejected"),
    ],
)
def test_diagnostic_must_preserve_actual_setup_exception_and_cause(
    invalid_check: dict,
    invalid_mutation: dict,
    stage: str,
    field: str,
    value: object,
) -> None:
    report = invalid_check if stage == "check" else invalid_mutation["baseline"]
    report["diagnostics"][0][field] = value

    with pytest.raises(RuntimeError, match="setup diagnostic"):
        if stage == "check":
            verify_invalid_check(invalid_check)
        else:
            verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize("stage", ["check", "mutation"])
@pytest.mark.parametrize("diagnostics", [[], None])
def test_missing_diagnostics_cannot_be_replaced_by_invalid_summary(
    invalid_check: dict,
    invalid_mutation: dict,
    stage: str,
    diagnostics: list | None,
) -> None:
    report = invalid_check if stage == "check" else invalid_mutation["baseline"]
    if diagnostics is None:
        del report["diagnostics"]
    else:
        report["diagnostics"] = diagnostics

    with pytest.raises(RuntimeError, match="missing setup diagnostic"):
        if stage == "check":
            verify_invalid_check(invalid_check)
        else:
            verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize(("field", "value"), [("status", "failed"), ("failures", 1)])
def test_invalid_check_cannot_claim_a_law_refutation(
    invalid_check: dict, field: str, value: object
) -> None:
    invalid_check["summary"][field] = value

    with pytest.raises(RuntimeError, match="counterexample or a valid check"):
        verify_invalid_check(invalid_check)


def test_setup_exception_cannot_become_a_retained_counterexample(
    invalid_check: dict,
) -> None:
    invalid_check["failures"] = [{"identity": "diagnostic_laws.py::still_passes"}]

    with pytest.raises(RuntimeError, match="counterexample or a valid check"):
        verify_invalid_check(invalid_check)


@pytest.mark.parametrize("stage", ["check", "mutation"])
def test_diagnostics_cannot_replace_invalid_obligation(
    invalid_check: dict, invalid_mutation: dict, stage: str
) -> None:
    report = invalid_check if stage == "check" else invalid_mutation["baseline"]
    report["obligations"][0]["status"] = "passed"

    with pytest.raises(RuntimeError, match="invalid obligation"):
        if stage == "check":
            verify_invalid_check(invalid_check)
        else:
            verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize(
    ("field", "value"), [("valid", True), ("exit_code", 0), ("timed_out", True)]
)
def test_invalid_baseline_cannot_claim_valid_execution(
    invalid_mutation: dict, field: str, value: object
) -> None:
    invalid_mutation["baseline"][field] = value

    with pytest.raises(RuntimeError, match="valid mutation baseline"):
        verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize("stage", ["canary", "run_count"])
def test_invalid_baseline_prevents_additional_runs(
    invalid_mutation: dict, stage: str
) -> None:
    if stage == "canary":
        invalid_mutation["canary"] = {"wired": True}
    else:
        invalid_mutation["work"]["runs_started"] = 2

    with pytest.raises(RuntimeError, match="additional isolated runs"):
        verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "killed"),
        ("exit_code", 1),
        ("timed_out", True),
        ("duration_seconds", 0.1),
        ("kill_kind", "observed"),
        ("failed_tests", ["diagnostic_laws.py::still_passes"]),
        ("evidence", {"diagnostics": []}),
        ("replay_evidence", {"summary": {"status": "passed"}}),
        ("timeout_baseline_evidence", {"diagnostics": []}),
        ("replay_method", "exact"),
        ("replay_exit_code", 0),
        ("replay_timed_out", False),
        ("replay_duration_seconds", 0.1),
    ],
)
def test_invalid_baseline_keeps_mutants_unrun_and_unconfirmed(
    invalid_mutation: dict, field: str, value: object
) -> None:
    invalid_mutation["mutations"][0][field] = value

    with pytest.raises(RuntimeError, match="unrun mutant claims"):
        verify_invalid_mutation(invalid_mutation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("not_run", 0),
        ("runnable", 1),
        ("killed", 1),
        ("complete", True),
        ("selection_complete", True),
        ("has_errors", False),
    ],
)
def test_invalid_baseline_cannot_claim_campaign_completion(
    invalid_mutation: dict, field: str, value: object
) -> None:
    invalid_mutation["summary"][field] = value

    with pytest.raises(RuntimeError, match="mutation completion or outcomes"):
        verify_invalid_mutation(invalid_mutation)
