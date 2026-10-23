"""Challenge installed selection receipts independently of mutation execution."""

from copy import deepcopy

import pytest

from scripts.check_installed import LAW, verify_selection_campaign


INVENTORY = ("123456789abc", "abcdef123456")


@pytest.fixture
def selection_report() -> dict:
    def obligation(status: str) -> dict:
        return {
            "identity": LAW,
            "status": status,
            "method": "exhaustive",
            "cases_checked": 3 if status == "passed" else 1,
            "cases_total": 3,
            "exhausted": status == "passed",
            "list_bounds": {},
        }

    untouched = {
        "outcome": "excluded",
        "selected": False,
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": 0.0,
        "kill_kind": None,
        "failed_tests": [],
        "evidence": {},
        "replay_evidence": {},
        "replay_method": None,
        "replay_exit_code": None,
        "replay_timed_out": None,
        "replay_duration_seconds": None,
    }
    selected = {**deepcopy(untouched), "id": INVENTORY[0], "selected": True}
    selected.update(
        outcome="killed",
        exit_code=1,
        duration_seconds=0.5,
        kill_kind="observed",
        failed_tests=[LAW],
        evidence={
            "summary": {"status": "failed", "retained": True},
            "obligations": [obligation("failed")],
            "failures": [{"identity": LAW, "capture_error": None}],
        },
        replay_method="exact",
        replay_exit_code=0,
        replay_timed_out=False,
        replay_duration_seconds=0.5,
        replay_evidence={
            "mode": "replay",
            "summary": {"status": "passed"},
            "obligations": [
                {
                    "identity": LAW,
                    "method": "replay",
                    "status": "passed",
                    "cases_checked": 1,
                    "cases_total": 1,
                }
            ],
        },
    )
    return {
        "schema": "tlaw.mutation/v1",
        "method": "exhaustive",
        "max_cases": 3,
        "plan_only": False,
        "plan": {
            "generated_mutations": 2,
            "selected_mutations": 1,
            "excluded_mutations": 1,
            "runnable_mutations": 1,
            "law_cases": 1,
            "cases_per_run": 3,
            "min_runs": 3,
            "max_runs": 5,
            "max_case_attempts": 15,
        },
        "work": {
            "max_runs": 4,
            "runs_started": 4,
            "runs_remaining": 0,
            "stopped_before": None,
        },
        "baseline": {"valid": True, "obligations": [obligation("passed")]},
        "canary": {
            "wired": True,
            "exit_code": 1,
            "timed_out": False,
            "obligations": [obligation("failed")],
        },
        "summary": {
            "generated": 2,
            "selected": 1,
            "excluded": 1,
            "not_run": 0,
            "selection_complete": True,
            "complete": False,
            "has_errors": False,
            "runnable": 1,
            "killed": 1,
            "survived": 0,
            "unresolved_equivalent": 0,
            "invalid": 0,
            "errors": 0,
        },
        "mutations": [selected, {**untouched, "id": INVENTORY[1]}],
    }


@pytest.fixture
def unrun_report(selection_report: dict) -> dict:
    selection_report["mutations"][0] = {
        **deepcopy(selection_report["mutations"][1]),
        "id": INVENTORY[0],
        "selected": True,
        "outcome": "not-run",
    }
    selection_report["work"].update(
        max_runs=2, runs_started=2, stopped_before=f"mutant {INVENTORY[0]}"
    )
    selection_report["summary"].update(
        not_run=1, selection_complete=False, has_errors=True, runnable=0, killed=0
    )
    return selection_report


def verify(report: dict, *, complete: bool = True) -> None:
    verify_selection_campaign(
        report, inventory=INVENTORY, selected_id=INVENTORY[0], complete=complete
    )


def test_selected_kill_preserves_full_inventory(selection_report: dict) -> None:
    verify(selection_report)


def test_budget_can_leave_selected_mutant_unrun(unrun_report: dict) -> None:
    verify(unrun_report, complete=False)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema",), "tlaw.check/v1"),
        (("method",), "sampled"),
        (("max_cases",), 1),
        (("mutations", 0, "id"), "1234"),
        (("mutations", 1, "id"), INVENTORY[0]),
        (("mutations", 0, "selected"), False),
        (("mutations", 1, "selected"), True),
        (("mutations", 1, "outcome"), "not-run"),
        (("mutations", 1, "exit_code"), 0),
        (("mutations", 1, "timed_out"), True),
        (("mutations", 1, "duration_seconds"), 0.5),
        (("mutations", 1, "kill_kind"), "observed"),
        (("mutations", 1, "failed_tests"), [LAW]),
        (("mutations", 1, "evidence"), {"retained": True}),
        (("mutations", 1, "replay_evidence"), {"mode": "replay"}),
        (("mutations", 1, "replay_method"), "exact"),
        (("mutations", 1, "replay_exit_code"), 0),
        (("mutations", 1, "replay_timed_out"), False),
        (("mutations", 1, "replay_duration_seconds"), 0.5),
        (("plan", "generated_mutations"), 1),
        (("plan", "selected_mutations"), 2),
        (("plan", "excluded_mutations"), 0),
        (("plan", "runnable_mutations"), 2),
        (("plan", "max_runs"), 8),
        (("summary", "generated"), 1),
        (("summary", "selected"), 2),
        (("summary", "excluded"), 0),
        (("summary", "not_run"), 1),
        (("summary", "selection_complete"), False),
        (("summary", "complete"), True),
        (("summary", "runnable"), 2),
        (("summary", "has_errors"), True),
        (("work", "runs_started"), 3),
        (("work", "stopped_before"), "canary"),
        (("baseline", "valid"), False),
        (("baseline", "obligations", 0, "cases_total"), 1),
        (("canary", "wired"), False),
        (("canary", "obligations", 0, "method"), "sampled"),
        (("mutations", 0, "replay_method"), None),
        (("mutations", 0, "replay_evidence", "obligations", 0, "cases_checked"), 0),
    ],
)
def test_incorrect_selection_receipt_is_rejected(
    selection_report: dict, path: tuple[str | int, ...], value: object
) -> None:
    target = selection_report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(RuntimeError):
        verify(selection_report)


def test_selected_budget_stop_cannot_claim_execution(unrun_report: dict) -> None:
    unrun_report["mutations"][0]["exit_code"] = 1

    with pytest.raises(RuntimeError, match="untouched mutant"):
        verify(unrun_report, complete=False)


def test_selection_cannot_drop_excluded_rows(selection_report: dict) -> None:
    selection_report["mutations"].pop()

    with pytest.raises(RuntimeError, match="generated inventory"):
        verify(selection_report)


def test_selection_cannot_reorder_generated_rows(selection_report: dict) -> None:
    selection_report["mutations"].reverse()

    with pytest.raises(RuntimeError, match="generated inventory"):
        verify(selection_report)
