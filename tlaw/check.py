"""Run, retain, and replay human-authored law evidence."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audit import TARGET_ENV, ManifestError
from .finite import DEFAULT_MAX_CASES, MAX_CASES_ENV
from .obligations import (
    OBLIGATIONS_ENV,
    Obligation,
    obligations_complete,
    read_obligations,
)
from .replay import (
    CAPTURE_ENV,
    MODE_ENV,
    REPLAY_ENV,
    REPLAY_ROOT_ENV,
    SEED_ENV,
    build_check_report,
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


def _environment(*, explain: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop(MAX_CASES_ENV, None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if explain:
        environment["TLAW_EXPLAIN"] = "1"
    else:
        environment.pop("TLAW_EXPLAIN", None)
    return environment


def _evidence_line(law: Obligation) -> str:
    if law.method == "sampled":
        return f"sampled: {law.nodeid}; total input space not enumerated"
    bounds = ""
    if law.list_bounds:
        lengths = ", ".join(
            f"{name}={bound['min_size']}..{bound['max_size']}"
            for name, bound in law.list_bounds.items()
        )
        bounds = f"; lengths: {lengths}"
    outcome = "all declared cases passed" if law.exhausted else "not exhausted"
    return (
        f"{law.method}: {law.nodeid}; "
        f"{law.cases_checked}/{law.cases_total} cases checked{bounds}; {outcome}"
    )


@dataclass(frozen=True)
class _PytestRun:
    exit_code: int
    output: str
    obligations: tuple[Obligation, ...]


def _run_pytest(
    law_path: Path,
    *,
    environment: dict[str, str],
    timeout_seconds: float,
) -> _PytestRun:
    with tempfile.TemporaryDirectory(prefix="tlaw-obligations-") as temporary:
        receipt = Path(temporary) / "obligations.json"
        environment = {**environment, OBLIGATIONS_ENV: str(receipt)}
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
            exit_code = completed.returncode
            output = completed.stdout + completed.stderr
        except subprocess.TimeoutExpired as error:
            stdout = (
                error.stdout.decode()
                if isinstance(error.stdout, bytes)
                else error.stdout
            )
            stderr = (
                error.stderr.decode()
                if isinstance(error.stderr, bytes)
                else error.stderr
            )
            exit_code = 124
            output = (stdout or "") + (stderr or "") + "\ntlaw: check timed out.\n"
        try:
            obligations = read_obligations(receipt)
        except ManifestError as error:
            return _PytestRun(exit_code, output + f"\ntlaw: {error}\n", ())

    count = sum(law.completed for law in obligations)
    output += f"\ntlaw obligations: {count} of {len(obligations)} completed\n"
    if not obligations:
        output += "tlaw: no tlaw laws were collected\n"
    for law in obligations:
        if not law.completed:
            output += f"  {law.status}: {law.nodeid}\n"
        output += f"  {_evidence_line(law)}\n"
    return _PytestRun(exit_code, output, obligations)


def check(
    path: str | Path,
    *,
    timeout_seconds: float = 60.0,
    explain: bool = False,
    search: bool = False,
    seed: int | None = None,
    exhaustive: bool = False,
    max_cases: int | None = None,
) -> CheckResult:
    """Run one laws.py file and retain its counterexamples.

    Normal inferred generation is deterministic. ``search=True`` chooses a
    fresh random seed unless one is supplied, then records that seed and the
    exact shrunk failure in :meth:`CheckResult.to_json`.
    ``exhaustive=True`` enumerates supported finite declarations up to
    ``max_cases`` combinations per law case, defaulting to 1,000.
    """
    law_path = Path(path).resolve()
    if law_path.suffix != ".py" or not law_path.is_file():
        raise ManifestError(f"law file does not exist or is not Python: {law_path}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if exhaustive and search:
        raise ValueError("exhaustive checking cannot be combined with search")
    if max_cases is not None:
        if not exhaustive:
            raise ValueError("max_cases requires exhaustive=True")
        if type(max_cases) is not int or max_cases <= 0:
            raise ValueError("max_cases must be a positive integer")
    if seed is not None:
        if not search:
            raise ValueError("seed is only meaningful with search=True")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")

    mode = "exhaustive" if exhaustive else "search" if search else "default"
    selected_seed = (
        seed if seed is not None else secrets.randbits(64) if search else None
    )
    environment = _environment(explain=explain)
    environment[MODE_ENV] = mode
    if exhaustive:
        environment[MAX_CASES_ENV] = str(
            DEFAULT_MAX_CASES if max_cases is None else max_cases
        )
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
        run = _run_pytest(
            law_path,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        captured = read_capture(capture_path)

    report = build_check_report(
        law_path,
        working_directory=working_directory,
        mode=mode,
        seed=selected_seed,
        pytest_exit_code=run.exit_code,
        captured=captured,
        obligations=run.obligations,
    )
    output = run.output
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
    if not obligations_complete(run.obligations) or run.exit_code not in (0, 1):
        exit_code = 2
    elif status == "passed":
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
            method = (
                "exhaustive" if result.report.get("mode") == "exhaustive" else "sampled"
            )
            lines.append(f"  {label} [{method}]  {result.path}")
            if method == "exhaustive":
                for entry in result.report.get("obligations", ()):
                    lines.append(f"    {_evidence_line(Obligation(**entry))}")
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
    exhaustive: bool = False,
    max_cases: int | None = None,
) -> FolderCheckResult:
    """Discover and check every law file under one directory.

    Sampled or exhaustive checks apply per file; search, replay, and JSON
    retention remain per-file evidence operations.
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
        check(
            law_file,
            timeout_seconds=timeout_seconds,
            explain=explain,
            exhaustive=exhaustive,
            max_cases=max_cases,
        )
        for law_file in law_files
    )
    return FolderCheckResult(directory=str(root), results=results)


def _replay_law_path(report: dict[str, Any], report_path: Path) -> tuple[Path, Path]:
    recorded = Path(report["law_file"])
    current_root = Path.cwd().resolve()
    candidates = (
        [(recorded, current_root)]
        if recorded.is_absolute()
        else [
            (current_root / recorded, current_root),
            (report_path.parent / recorded, report_path.parent),
        ]
    )
    for candidate, identity_root in candidates:
        resolved = candidate.resolve()
        if resolved.suffix == ".py" and resolved.is_file():
            return resolved, identity_root.resolve()
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
    law_path, identity_root = _replay_law_path(report, path)
    environment = _environment(explain=False)
    environment.pop(CAPTURE_ENV, None)
    environment.pop(MODE_ENV, None)
    environment.pop(SEED_ENV, None)
    environment[REPLAY_ENV] = str(path)
    environment[REPLAY_ROOT_ENV] = str(identity_root)
    run = _run_pytest(
        law_path,
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    output = run.output
    expected = {failure["identity"] for failure in report["failures"]}
    observed = {law.identity for law in run.obligations}
    if not obligations_complete(run.obligations) or expected != observed:
        exit_code = 2
    elif run.exit_code == 0:
        count = len(report["failures"])
        noun = "counterexample" if count == 1 else "counterexamples"
        output = f"tlaw replay: {count} retained {noun} reproduced\n{output}"
        exit_code = 0
    elif run.exit_code == 1:
        exit_code = 1
    else:
        exit_code = 2
    return CheckResult(
        path=str(law_path),
        exit_code=exit_code,
        output=output,
        report={**report, "obligations": [law.to_dict() for law in run.obligations]},
    )


__all__ = [
    "CheckResult",
    "FolderCheckResult",
    "check",
    "check_folder",
    "discover_law_files",
    "replay",
]
