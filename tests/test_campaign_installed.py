"""Challenge the installed gate's campaign receipts independently of the runner."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.check_installed import verify_campaign_budget


@pytest.fixture
def planned_report() -> dict:
    return {
        "schema": "tlaw.mutation/v1",
        "method": "exhaustive",
        "max_cases": 3,
        "plan_only": True,
        "plan": {
            "generated_mutations": 1,
            "runnable_mutations": 1,
            "law_cases": 1,
            "cases_per_run": 3,
            "min_runs": 3,
            "max_runs": 5,
            "max_case_attempts": 15,
        },
        "work": {
            "max_runs": 1,
            "runs_started": 1,
            "runs_remaining": 0,
            "stopped_before": None,
        },
        "baseline": {
            "valid": True,
            "obligations": [
                {
                    "identity": "laws.py::increment_advances_once",
                    "status": "passed",
                    "method": "exhaustive",
                    "cases_checked": 3,
                    "cases_total": 3,
                    "exhausted": True,
                    "list_bounds": {},
                }
            ],
        },
        "canary": None,
        "summary": {
            "generated": 1,
            "runnable": 0,
            "killed": 0,
            "survived": 0,
            "unresolved_equivalent": 0,
            "invalid": 0,
            "errors": 0,
            "not_run": 1,
            "complete": False,
            "has_errors": False,
        },
        "mutations": [
            {
                "id": "increment:operator:1234",
                "outcome": "not-run",
                "exit_code": None,
                "kill_kind": None,
                "failed_tests": [],
                "evidence": {},
                "replay_method": None,
                "replay_evidence": {},
                "replay_exit_code": None,
                "replay_timed_out": None,
                "replay_duration_seconds": None,
            }
        ],
    }


@pytest.fixture
def blocked_report(planned_report: dict) -> dict:
    planned_report["plan_only"] = False
    planned_report["work"].update(
        max_runs=3,
        runs_started=3,
        stopped_before="exact replay for increment:operator:1234",
    )
    planned_report["canary"] = {"wired": True}
    planned_report["summary"].update(not_run=0, errors=1, has_errors=True)
    failed = deepcopy(planned_report["baseline"]["obligations"][0])
    failed.update(status="failed", cases_checked=1, exhausted=False)
    planned_report["mutations"][0].update(
        outcome="error",
        exit_code=1,
        note="confirmation not run: campaign run budget exhausted",
        evidence={
            "summary": {"status": "failed", "retained": True},
            "obligations": [failed],
            "failures": [
                {"identity": "laws.py::increment_advances_once", "capture_error": None}
            ],
        },
    )
    return planned_report


def test_baseline_only_plan_is_accepted(planned_report: dict) -> None:
    verify_campaign_budget(planned_report, stage="plan")


def test_unconfirmed_failure_history_is_accepted(blocked_report: dict) -> None:
    verify_campaign_budget(blocked_report, stage="blocked")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generated_mutations", 0),
        ("runnable_mutations", 2),
        ("law_cases", 2),
        ("cases_per_run", None),
        ("min_runs", 1),
        ("max_runs", 4),
        ("max_case_attempts", 12),
    ],
)
def test_plan_requires_full_campaign_estimates(
    planned_report: dict, field: str, value: object
) -> None:
    planned_report["plan"][field] = value

    with pytest.raises(RuntimeError, match="unexpected campaign plan"):
        verify_campaign_budget(planned_report, stage="plan")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_runs", 2),
        ("runs_started", 0),
        ("runs_remaining", 1),
        ("stopped_before", "canary"),
    ],
)
def test_plan_requires_actual_baseline_run_accounting(
    planned_report: dict, field: str, value: object
) -> None:
    planned_report["work"][field] = value

    with pytest.raises(RuntimeError, match="work accounting"):
        verify_campaign_budget(planned_report, stage="plan")


@pytest.mark.parametrize(
    ("field", "value"),
    [("complete", True), ("not_run", 0), ("killed", 1), ("runnable", 1)],
)
def test_planning_cannot_claim_mutation_evidence(
    planned_report: dict, field: str, value: object
) -> None:
    planned_report["summary"][field] = value

    with pytest.raises(RuntimeError):
        verify_campaign_budget(planned_report, stage="plan")


@pytest.mark.parametrize("stage", ["canary", "mutant"])
def test_planning_cannot_start_additional_children(
    planned_report: dict, stage: str
) -> None:
    if stage == "canary":
        planned_report["canary"] = {"wired": True}
    else:
        planned_report["mutations"][0]["exit_code"] = 0

    with pytest.raises(RuntimeError, match="unexpectedly ran"):
        verify_campaign_budget(planned_report, stage="plan")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "killed"),
        ("kill_kind", "observed"),
        ("failed_tests", ["laws.py::increment_advances_once"]),
        ("replay_method", "exact"),
        ("replay_evidence", {"summary": {"status": "passed"}}),
        ("replay_exit_code", 0),
        ("replay_timed_out", False),
        ("replay_duration_seconds", 0.1),
    ],
)
def test_unrun_confirmation_cannot_claim_a_kill_or_replay(
    blocked_report: dict, field: str, value: object
) -> None:
    blocked_report["mutations"][0][field] = value

    with pytest.raises(RuntimeError):
        verify_campaign_budget(blocked_report, stage="blocked")


def test_blocked_confirmation_keeps_the_actual_failure(blocked_report: dict) -> None:
    blocked_report["mutations"][0]["evidence"]["summary"]["retained"] = False

    with pytest.raises(RuntimeError, match="failure history was not retained"):
        verify_campaign_budget(blocked_report, stage="blocked")


def test_blocked_phase_names_the_affected_mutant(blocked_report: dict) -> None:
    blocked_report["work"]["stopped_before"] = "exact replay for another mutant"

    with pytest.raises(RuntimeError, match="work accounting"):
        verify_campaign_budget(blocked_report, stage="blocked")
