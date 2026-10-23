"""The one child-pytest runner behind ``tlaw check``, ``mutate``, and ``audit``.

Every tlaw command that executes laws or declared tests runs pytest in a child
process. The child sees a deliberately narrowed environment — the parent's tlaw
control variables cleared, third-party pytest plugin autoload off, hashing
pinned, bytecode writes suppressed — because that environment is part of what
makes a run reproducible and what keeps a probed project unmodified. Those
rules used to be written three times and drifted apart; they live here now.

The commands still differ on purpose. A law run is tlaw-owned: quiet output, no
pytest cache provider, the tlaw report plugin, and the target project's
``addopts`` cleared. The legacy TOML audit runs the manifest's own pytest
selection instead. Those differences are explicit arguments rather than hidden
branches, so a caller cannot inherit another command's policy by accident.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


#: Environment names a law run must never inherit from the parent process.
#: The values are spelled out here because the modules that define them import
#: this one; ``tests/test_execution.py`` keeps the two in sync. ``check`` and
#: ``mutate`` clear exactly this set so one invocation's target, mode, seed, or
#: receipt cannot leak into a nested run.
LAW_RUN_CLEARED_ENV = (
    "TLAW_EXPLAIN",
    "TLAW_OBLIGATIONS",
    "TLAW_CAPTURE",
    "TLAW_MODE",
    "TLAW_REPLAY",
    "TLAW_REPLAY_ROOT",
    "TLAW_SEED",
    "TLAW_MAX_CASES",
    "TLAW_TARGET",
    "PYTEST_ADDOPTS",
)


def pytest_argv(
    targets: Sequence[str] = (),
    *,
    options: Sequence[str] = (),
    tlaw_owned: bool = True,
) -> tuple[str, ...]:
    """Build the pytest argv for one child process.

    ``tlaw_owned`` selects the law-run form: quiet, no cache provider, the
    tlaw report plugin loaded explicitly, and the target's ``addopts``
    cleared. The target project's flags are cleared because tlaw disables
    third-party plugin autoload, so an inherited flag naming an absent plugin
    (``-n`` from xdist, for instance) would turn every run into a usage error.
    Probing a codebase must not require editing that codebase.

    ``options`` are the caller's own pytest arguments; ``targets`` are the
    paths or node IDs to collect. For the manifest-driven audit form both the
    manifest's arguments and its test paths arrive through ``options``.
    """
    command = [sys.executable, "-m", "pytest"]
    if tlaw_owned:
        command += (
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "tlaw.pytest_plugin",
            "-o",
            "addopts=",
        )
    command += list(options)
    command += list(targets)
    return tuple(command)


def child_environment(
    *,
    clear: Sequence[str] = (),
    update: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the environment for one child pytest process.

    The inherited environment is copied, ``clear`` removes tlaw control
    variables the parent may be running under, and the isolation rules every
    tlaw child shares are applied:

    - ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` — a target project's installed
      plugins must not decide how tlaw runs;
    - ``PYTHONHASHSEED=0`` — exact counterexample replay depends on stable
      hashing in the child;
    - ``PYTHONDONTWRITEBYTECODE=1`` — importing a probed project's modules
      must not leave ``__pycache__`` behind.

    ``update`` applies this invocation's own values last, so they win over
    both the inherited environment and the shared defaults.
    """
    environment = os.environ.copy()
    for name in clear:
        environment.pop(name, None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if update:
        environment.update(update)
    return environment


@dataclass(frozen=True)
class ChildRun:
    """One finished child pytest process.

    ``output`` is stdout and stderr combined, exactly as pytest printed them.
    A timed-out run has ``exit_code == 124`` and ``timed_out is True``; the
    caller appends its own timeout message because check, audit, and mutation
    each phrase that outcome differently.
    """

    exit_code: int
    timed_out: bool
    duration_seconds: float
    output: str
    command: tuple[str, ...]


def run_pytest(
    command: Sequence[str],
    *,
    cwd: str | Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> ChildRun:
    """Run one child pytest process and return its observed result.

    The process runs with captured output and never raises for a nonzero exit:
    a failing test is evidence, not an exception. Only the caller decides what
    an exit code means.
    """
    command = tuple(command)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(environment),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
        )
        stderr = (
            error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        )
        output = (stdout or "") + (stderr or "")
        exit_code = 124
        timed_out = True
    return ChildRun(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=time.monotonic() - started,
        output=output,
        command=command,
    )
