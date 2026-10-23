"""Probes for the shared child-pytest runner.

The runner's argv and environment are part of tlaw's evidence contract, so
these tests pin the two forms — tlaw-owned law runs and manifest-driven audit
runs — and the isolation rules every child shares.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tlaw import _execution
from tlaw.audit import TARGET_ENV
from tlaw.finite import MAX_CASES_ENV
from tlaw.obligations import OBLIGATIONS_ENV
from tlaw.replay import (
    CAPTURE_ENV,
    MODE_ENV,
    REPLAY_ENV,
    REPLAY_ROOT_ENV,
    SEED_ENV,
)


def test_a_law_run_owns_its_pytest_invocation() -> None:
    assert _execution.pytest_argv(("laws.py",)) == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tlaw.pytest_plugin",
        "-o",
        "addopts=",
        "laws.py",
    )


def test_callers_append_their_own_options_in_order() -> None:
    assert _execution.pytest_argv(
        ("/tmp/laws.py",), options=("--import-mode=importlib",)
    ) == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tlaw.pytest_plugin",
        "-o",
        "addopts=",
        "--import-mode=importlib",
        "/tmp/laws.py",
    )


def test_a_manifest_run_keeps_the_manifest_selection() -> None:
    # The legacy TOML audit declares its own pytest flags and tests; tlaw does
    # not impose its quiet/plugin/addopts policy on that selection.
    assert _execution.pytest_argv(options=("-q", "test_laws.py"), tlaw_owned=False) == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "test_laws.py",
    )


def test_every_child_gets_the_shared_isolation_defaults() -> None:
    environment = _execution.child_environment()

    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_parent_hash_seed_and_control_variables_never_reach_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    monkeypatch.setenv(MODE_ENV, "search")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 4 --dist loadgroup")
    monkeypatch.setenv(TARGET_ENV, "implementations:Wrong")

    environment = _execution.child_environment(clear=_execution.LAW_RUN_CLEARED_ENV)

    assert environment["PYTHONHASHSEED"] == "0"
    assert MODE_ENV not in environment
    assert "PYTEST_ADDOPTS" not in environment
    assert TARGET_ENV not in environment


def test_a_caller_update_wins_over_inherited_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TLAW_EXPLAIN", "1")

    environment = _execution.child_environment(update={"TLAW_EXPLAIN": "0"})

    assert environment["TLAW_EXPLAIN"] == "0"


def test_the_cleared_names_stay_in_sync_with_the_modules_that_define_them() -> None:
    """The runner cannot import these modules without a cycle, so pin the set."""
    assert set(_execution.LAW_RUN_CLEARED_ENV) == {
        "TLAW_EXPLAIN",
        TARGET_ENV,
        "PYTEST_ADDOPTS",
        CAPTURE_ENV,
        MODE_ENV,
        OBLIGATIONS_ENV,
        MAX_CASES_ENV,
        SEED_ENV,
        REPLAY_ENV,
        REPLAY_ROOT_ENV,
    }


def test_a_timeout_is_reported_as_one_result_not_an_exception(
    tmp_path: Path,
) -> None:
    run = _execution.run_pytest(
        (sys.executable, "-c", "import time; time.sleep(10)"),
        cwd=tmp_path,
        environment=os.environ,
        timeout_seconds=0.05,
    )

    assert run.timed_out is True
    assert run.exit_code == 124
    assert run.duration_seconds < 10


def test_a_failing_child_is_a_result_not_an_exception(tmp_path: Path) -> None:
    run = _execution.run_pytest(
        (sys.executable, "-c", "raise SystemExit(3)"),
        cwd=tmp_path,
        environment=os.environ,
        timeout_seconds=10,
    )

    assert run.timed_out is False
    assert run.exit_code == 3
