"""Challenge the installed gate's evidence rather than its command sequence."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from scripts.check_installed import clean_environment, run, verify_mutation


@pytest.fixture
def killed_report() -> dict:
    """The public report subset needed to support one exact mutant kill."""
    identity = "laws.py::increment_advances_once"

    def obligation(status: str) -> dict:
        return {
            "identity": identity,
            "status": status,
            "method": "sampled",
            "cases_checked": None,
            "cases_total": None,
            "exhausted": False,
            "list_bounds": {},
        }

    return {
        "schema": "tlaw.mutation/v1",
        "method": "sampled",
        "max_cases": None,
        "baseline": {
            "valid": True,
            "obligations": [obligation("passed")],
        },
        "canary": {
            "wired": True,
            "exit_code": 1,
            "timed_out": False,
            "obligations": [obligation("failed")],
        },
        "summary": {
            "runnable": 1,
            "killed": 1,
            "survived": 0,
            "unresolved_equivalent": 0,
            "invalid": 0,
            "errors": 0,
            "has_errors": False,
        },
        "mutations": [
            {
                "outcome": "killed",
                "kill_kind": "observed",
                "replay_method": "exact",
                "replay_exit_code": 0,
                "replay_timed_out": False,
                "evidence": {
                    "summary": {"status": "failed", "retained": True},
                    "failures": [{"identity": identity, "capture_error": None}],
                    "obligations": [obligation("failed")],
                },
                "replay_evidence": {
                    "mode": "replay",
                    "summary": {"status": "passed"},
                    "obligations": [
                        {
                            "identity": identity,
                            "method": "replay",
                            "status": "passed",
                            "cases_checked": 1,
                            "cases_total": 1,
                        }
                    ],
                },
            }
        ],
    }


@pytest.fixture
def exhaustive_report(killed_report: dict) -> dict:
    killed_report["method"] = "exhaustive"
    killed_report["max_cases"] = 3
    obligations = [
        killed_report["baseline"]["obligations"][0],
        killed_report["canary"]["obligations"][0],
        killed_report["mutations"][0]["evidence"]["obligations"][0],
    ]
    for obligation in obligations:
        passed = obligation["status"] == "passed"
        obligation.update(
            method="exhaustive",
            cases_checked=3 if passed else 1,
            cases_total=3,
            exhausted=passed,
        )
    return killed_report


def test_exact_kill_evidence_is_accepted(killed_report: dict) -> None:
    verify_mutation(killed_report)


def test_exhaustive_kill_evidence_is_accepted(exhaustive_report: dict) -> None:
    verify_mutation(exhaustive_report, method="exhaustive")


@pytest.mark.parametrize("stage", ["baseline", "canary", "mutant"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cases_total", 1),
        ("cases_checked", 0),
        ("exhausted", None),
        ("list_bounds", {"value": {"min_size": 0, "max_size": 1}}),
        ("method", "sampled"),
    ],
)
def test_exhaustive_receipts_cannot_narrow_the_domain(
    exhaustive_report: dict, stage: str, field: str, value: object
) -> None:
    report = (
        exhaustive_report["mutations"][0]["evidence"]
        if stage == "mutant"
        else exhaustive_report[stage]
    )
    report["obligations"][0][field] = value

    with pytest.raises(RuntimeError, match=f"unexpected {stage}"):
        verify_mutation(exhaustive_report, method="exhaustive")


@pytest.mark.parametrize("stage", ["baseline", "canary", "mutant"])
@pytest.mark.parametrize("field", ["cases_checked", "cases_total"])
def test_exhaustive_receipts_require_case_counts(
    exhaustive_report: dict, stage: str, field: str
) -> None:
    report = (
        exhaustive_report["mutations"][0]["evidence"]
        if stage == "mutant"
        else exhaustive_report[stage]
    )
    del report["obligations"][0][field]

    with pytest.raises(RuntimeError, match=f"unexpected {stage} exhaustive counts"):
        verify_mutation(exhaustive_report, method="exhaustive")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("method", "sampled", "mutation method"),
        ("max_cases", 1, "mutation case cap"),
    ],
)
def test_exhaustive_campaign_requires_its_requested_mode_and_cap(
    exhaustive_report: dict, field: str, value: object, message: str
) -> None:
    exhaustive_report[field] = value

    with pytest.raises(RuntimeError, match=message):
        verify_mutation(exhaustive_report, method="exhaustive")


def test_killed_summary_without_exact_replay_is_rejected(killed_report: dict) -> None:
    killed_report["mutations"][0]["replay_method"] = None

    with pytest.raises(RuntimeError, match="lacks exact replay"):
        verify_mutation(killed_report)


def test_zero_replayed_cases_cannot_confirm_a_kill(killed_report: dict) -> None:
    replay = killed_report["mutations"][0]["replay_evidence"]
    replay["obligations"][0]["cases_checked"] = 0

    with pytest.raises(RuntimeError, match="not replayed exactly once"):
        verify_mutation(killed_report)


def test_unretained_failure_cannot_confirm_a_kill(killed_report: dict) -> None:
    killed_report["mutations"][0]["evidence"]["summary"]["retained"] = False

    with pytest.raises(RuntimeError, match="failure not retained"):
        verify_mutation(killed_report)


def test_capture_error_cannot_confirm_a_kill(killed_report: dict) -> None:
    failure = killed_report["mutations"][0]["evidence"]["failures"][0]
    failure["capture_error"] = "unsupported input type"

    with pytest.raises(RuntimeError, match="input not replayable"):
        verify_mutation(killed_report)


def test_gate_clears_inherited_execution_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = {
        "TLAW_REPLAY": "old-report.json",
        "TLAW_MODE": "exhaustive",
        "PYTEST_ADDOPTS": "--collect-only",
        "PYTEST_PLUGINS": "unexpected_plugin",
        "PYTHONPATH": "/unexpected/source",
        "PYTHONHOME": "/unexpected/python",
        "VIRTUAL_ENV": "/old/venv",
        "UV_PROJECT_ENVIRONMENT": "/old/project-venv",
    }
    for name, value in poisoned.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UV_CACHE_DIR", "/retained/cache")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")

    environment = clean_environment()

    assert all(name not in environment for name in poisoned)
    assert environment["UV_CACHE_DIR"] == "/retained/cache"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONHASHSEED"] == "0"
    assert os.environ["TLAW_REPLAY"] == "old-report.json"


def test_success_text_cannot_hide_a_wrong_process_exit(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="expected exit 0, got 1") as error:
        run(
            [sys.executable, "-c", "print('PASS'); raise SystemExit(1)"],
            cwd=tmp_path,
            environment=clean_environment(),
        )

    assert "PASS" in str(error.value)
