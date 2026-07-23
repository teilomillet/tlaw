"""Run, retain, and replay human-authored law evidence."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hypothesis

from .audit import TARGET_ENV, ManifestError
from .replay import (
    CAPTURE_ENV,
    MODE_ENV,
    REPLAY_ENV,
    REPLAY_ROOT_ENV,
    REPORT_SCHEMA,
    SEED_ENV,
    load_report,
    read_capture,
)


@dataclass(frozen=True)
class CheckResult:
    path: str
    exit_code: int
    output: str
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def to_json(self) -> str:
        """Return the stable retained-evidence report."""
        return json.dumps(self.report, indent=2, sort_keys=True) + "\n"


def _pytest_command(law_path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tlaw.pytest_plugin",
        # The target project's ``addopts`` are cleared, not inherited. tlaw
        # owns this invocation and disables third-party plugin autoload, so
        # an inherited flag from a plugin that is now absent (``-n`` from
        # xdist, for instance) turns every check into a pytest usage error.
        # Probing a codebase must not require editing that codebase, so the
        # conflict is resolved here rather than in the target's config.
        "-o",
        "addopts=",
        law_path.name,
    )


def _portable_path(path: Path, *, working_directory: Path) -> str:
    try:
        return str(path.relative_to(working_directory))
    except ValueError:
        return str(path)


def _public_report(
    law_path: Path,
    *,
    working_directory: Path,
    mode: str,
    seed: int | None,
    pytest_exit_code: int,
    captured: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    portable = _portable_path(law_path, working_directory=working_directory)
    failures = []
    for raw in captured:
        failure = dict(raw)
        source_path = Path(failure.pop("source_path"))
        source = (
            portable
            if source_path == law_path
            else _portable_path(source_path, working_directory=working_directory)
        )
        failure["source"] = {
            "path": source,
            "line": failure.pop("source_line"),
        }
        failure["identity"] = f"{source}::{failure['name']}"
        failures.append(failure)
    failures.sort(key=lambda failure: failure["identity"])

    # A counterexample that cannot be encoded is a limit of the *evidence*,
    # never a claim about the code under law: `capture_error` is only ever
    # "this input type has no safe replay encoding". Reporting the run as
    # invalid because of it would erase the thing tlaw actually established
    # — that the law was refuted — so the two facts are kept apart. The
    # refutation decides `status`; retention is its own field.
    unretained = [
        failure["capture_error"]
        for failure in failures
        if failure.get("capture_error") is not None
    ]
    if pytest_exit_code == 0:
        status = "passed"
    elif pytest_exit_code == 1 and failures:
        status = "failed"
    else:
        status = "invalid"
    return {
        "schema": REPORT_SCHEMA,
        "law_file": portable,
        "law_sha256": hashlib.sha256(law_path.read_bytes()).hexdigest(),
        "mode": mode,
        "seed": seed,
        "hypothesis_version": hypothesis.__version__,
        "pytest_exit_code": pytest_exit_code,
        "summary": {
            "failures": len(failures),
            "status": status,
            # False when at least one retained failure has no replayable
            # inputs. Separate from `status` so "the law was refuted" and
            # "the counterexample could not be retained" never overwrite
            # each other.
            "retained": not unretained,
        },
        "failures": failures,
    }


def _environment(*, explain: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if explain:
        environment["TLAW_EXPLAIN"] = "1"
    else:
        environment.pop("TLAW_EXPLAIN", None)
    return environment


def _run_pytest(
    law_path: Path,
    *,
    environment: dict[str, str],
    timeout_seconds: float,
) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            _pytest_command(law_path),
            cwd=law_path.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
        )
        stderr = (
            error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        )
        return 124, (stdout or "") + (stderr or "") + "\ntlaw: check timed out.\n"


def check(
    path: str | Path,
    *,
    timeout_seconds: float = 60.0,
    explain: bool = False,
    search: bool = False,
    seed: int | None = None,
) -> CheckResult:
    """Run one laws.py file and retain any minimized counterexamples.

    Normal inferred generation is deterministic. ``search=True`` chooses a
    fresh random seed unless one is supplied, then records that seed and the
    exact shrunk failure in :meth:`CheckResult.to_json`.
    """
    law_path = Path(path).resolve()
    if law_path.suffix != ".py" or not law_path.is_file():
        raise ManifestError(f"law file does not exist or is not Python: {law_path}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if seed is not None:
        if not search:
            raise ValueError("seed is only meaningful with search=True")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")

    mode = "search" if search else "default"
    selected_seed = (
        seed if seed is not None else secrets.randbits(64) if search else None
    )
    environment = _environment(explain=explain)
    environment[MODE_ENV] = mode
    if selected_seed is None:
        environment.pop(SEED_ENV, None)
    else:
        environment[SEED_ENV] = str(selected_seed)
    environment.pop(REPLAY_ENV, None)
    environment.pop(REPLAY_ROOT_ENV, None)
    working_directory = Path.cwd().resolve()

    with tempfile.TemporaryDirectory(prefix="tlaw-check-") as temporary:
        capture_path = Path(temporary) / "counterexamples.json"
        environment[CAPTURE_ENV] = str(capture_path)
        pytest_exit_code, output = _run_pytest(
            law_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        captured = read_capture(capture_path)

    report = _public_report(
        law_path,
        working_directory=working_directory,
        mode=mode,
        seed=selected_seed,
        pytest_exit_code=pytest_exit_code,
        captured=captured,
    )
    if search:
        output = f"tlaw search seed: {selected_seed}\n{output}"

    capture_errors = [
        failure["capture_error"]
        for failure in report["failures"]
        if failure.get("capture_error") is not None
    ]
    if capture_errors:
        output += (
            "\ntlaw: the law above was refuted, but this counterexample "
            "cannot be retained for replay:\n"
        )
        output += "\n".join(f"  {message}" for message in capture_errors)
        output += "\n"

    status = report["summary"]["status"]
    if status == "passed":
        exit_code = 0
    elif status == "failed":
        exit_code = 1
    else:
        exit_code = 2
    return CheckResult(
        path=str(law_path),
        exit_code=exit_code,
        output=output,
        report=report,
    )


@dataclass(frozen=True)
class FolderCheckResult:
    """The aggregate of checking every laws.py under one directory.

    A folder passes only when every law file passes; its exit code is the
    worst any file reported (2 error beats 1 failure beats 0 pass), so the
    convenience of one command never hides a single failing file.
    """

    directory: str
    results: tuple[CheckResult, ...]

    @property
    def exit_code(self) -> int:
        return max((result.exit_code for result in self.results), default=2)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        lines = [f"tlaw check {self.directory}: {len(self.results)} law file(s)"]
        for result in self.results:
            label = {0: "PASS", 1: "FAIL", 2: "ERROR"}.get(result.exit_code, "ERROR")
            lines.append(f"  {label}  {result.path}")
        passed = sum(result.passed for result in self.results)
        lines.append(f"{passed} of {len(self.results)} law file(s) passed")
        return "\n".join(lines) + "\n"


def discover_law_files(directory: Path) -> tuple[Path, ...]:
    """Return every ``laws.py`` or ``*_laws.py`` under a directory, sorted.

    The documented feature shape is ``implementation.py`` beside
    ``laws.py``; larger folders may split laws across ``*_laws.py`` files.
    Discovery is recursive and deterministically ordered so the aggregate
    result is reproducible. Cache and dot directories below the selected
    directory are skipped; selecting a hidden directory itself still works.
    """
    found: list[Path] = []
    for path in directory.rglob("*laws.py"):
        if not path.is_file():
            continue
        if path.name != "laws.py" and not path.name.endswith("_laws.py"):
            continue
        relative_parts = path.relative_to(directory).parts
        if any(
            part == "__pycache__" or part.startswith(".") for part in relative_parts
        ):
            continue
        found.append(path)
    return tuple(sorted(found))


def check_folder(
    directory: str | Path,
    *,
    timeout_seconds: float = 60.0,
    explain: bool = False,
) -> FolderCheckResult:
    """Discover and check every law file under one directory.

    Only the plain check applies per file: search, replay, and JSON
    retention are per-file evidence operations and are not folder modes.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ManifestError(f"not a directory: {root}")
    law_files = discover_law_files(root)
    if not law_files:
        raise ManifestError(
            f"no laws.py or *_laws.py found under {root}; check names a file "
            "or a folder that contains law files"
        )
    results = tuple(
        check(law_file, timeout_seconds=timeout_seconds, explain=explain)
        for law_file in law_files
    )
    return FolderCheckResult(directory=str(root), results=results)


def _replay_law_path(report: dict[str, Any], report_path: Path) -> Path:
    recorded = Path(report["law_file"])
    candidates = (
        [recorded]
        if recorded.is_absolute()
        else [
            Path.cwd() / recorded,
            report_path.parent / recorded,
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.suffix == ".py" and resolved.is_file():
            return resolved
    raise ManifestError(
        f"replay law file does not exist: {report['law_file']}; "
        "run replay from the repository root or restore that path"
    )


def replay(
    report_path: str | Path,
    *,
    timeout_seconds: float = 60.0,
) -> CheckResult:
    """Rerun exactly the failures retained in one ``tlaw.check/v1`` report."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    path = Path(report_path).resolve()
    report = load_report(path)
    law_path = _replay_law_path(report, path)
    environment = _environment(explain=False)
    environment.pop(CAPTURE_ENV, None)
    environment.pop(MODE_ENV, None)
    environment.pop(SEED_ENV, None)
    environment[REPLAY_ENV] = str(path)
    environment[REPLAY_ROOT_ENV] = str(Path.cwd().resolve())
    pytest_exit_code, output = _run_pytest(
        law_path,
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    if pytest_exit_code == 0:
        count = len(report["failures"])
        noun = "counterexample" if count == 1 else "counterexamples"
        output = f"tlaw replay: {count} retained {noun} reproduced\n{output}"
        exit_code = 0
    elif pytest_exit_code == 1:
        exit_code = 1
    else:
        exit_code = 2
    return CheckResult(
        path=str(law_path),
        exit_code=exit_code,
        output=output,
        report=report,
    )


__all__ = [
    "CheckResult",
    "FolderCheckResult",
    "check",
    "check_folder",
    "discover_law_files",
    "replay",
]
