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
    return {
        "schema": "tlaw.mutation/v1",
        "baseline": {
            "valid": True,
            "obligations": [{"identity": identity, "status": "passed"}],
        },
        "canary": {"wired": True, "exit_code": 1, "timed_out": False},
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


def test_exact_kill_evidence_is_accepted(killed_report: dict) -> None:
    verify_mutation(killed_report)


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
