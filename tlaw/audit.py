from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


TARGET_ENV = "TLAW_TARGET"
_FAILED_TEST_RE = re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE)


class ManifestError(ValueError):
    """Raised when an audit manifest cannot describe trustworthy evidence."""


class EnforcementError(RuntimeError):
    """Raised when an audit cannot justify accepting the declared laws."""

    def __init__(self, message: str, *, result: AuditResult) -> None:
        super().__init__(message)
        self.result = result


class KnownWrongOutcome(Enum):
    REJECTED = "rejected"
    SURVIVED = "survived"
    ERROR = "error"


@dataclass(frozen=True)
class Candidate:
    name: str
    target: str
    breaks: str | None = None


@dataclass(frozen=True)
class AuditManifest:
    path: Path
    tests: tuple[str, ...]
    pytest_args: tuple[str, ...]
    timeout_seconds: float
    baseline: Candidate
    known_wrong: tuple[Candidate, ...]

    @property
    def root(self) -> Path:
        return self.path.parent


@dataclass(frozen=True)
class RunResult:
    name: str
    target: str
    exit_code: int
    failed_tests: tuple[str, ...] = ()
    output: str = ""
    command: tuple[str, ...] = ()

    @property
    def known_wrong_outcome(self) -> KnownWrongOutcome:
        if self.exit_code == 0:
            return KnownWrongOutcome.SURVIVED
        if self.exit_code == 1:
            return KnownWrongOutcome.REJECTED
        return KnownWrongOutcome.ERROR


@dataclass(frozen=True)
class AuditResult:
    manifest: str
    baseline: RunResult
    known_wrong: tuple[RunResult, ...]

    @property
    def baseline_valid(self) -> bool:
        return self.baseline.exit_code == 0

    @property
    def total_known_wrong(self) -> int:
        return len(self.known_wrong)

    @property
    def rejected_count(self) -> int:
        return sum(
            run.known_wrong_outcome is KnownWrongOutcome.REJECTED
            for run in self.known_wrong
        )

    @property
    def score(self) -> float:
        if not self.known_wrong:
            return 0.0
        return self.rejected_count / self.total_known_wrong

    @property
    def has_errors(self) -> bool:
        return (not self.baseline_valid) or any(
            run.known_wrong_outcome is KnownWrongOutcome.ERROR
            for run in self.known_wrong
        )

    @property
    def sufficient(self) -> bool:
        return (
            self.baseline_valid
            and bool(self.known_wrong)
            and not self.has_errors
            and self.rejected_count == self.total_known_wrong
        )

    def outcome_for(self, name: str) -> KnownWrongOutcome:
        for run in self.known_wrong:
            if run.name == name:
                return run.known_wrong_outcome
        raise KeyError(name)

    def enforce(self) -> AuditResult:
        """Return this result if sufficient, otherwise stop the caller."""
        if self.sufficient:
            return self

        if self.has_errors:
            message = "tlaw could not produce valid evidence."
        else:
            survivors = [
                run.name
                for run in self.known_wrong
                if run.known_wrong_outcome is KnownWrongOutcome.SURVIVED
            ]
            detail = f" Survived: {', '.join(survivors)}." if survivors else ""
            message = f"tlaw found insufficient laws.{detail}"
        raise EnforcementError(message, result=self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "tlaw.audit/v1",
            "manifest": self.manifest,
            "baseline": {
                "name": self.baseline.name,
                "target": self.baseline.target,
                "exit_code": self.baseline.exit_code,
                "valid": self.baseline_valid,
                "failed_tests": list(self.baseline.failed_tests),
            },
            "known_wrong": [
                {
                    "name": run.name,
                    "target": run.target,
                    "exit_code": run.exit_code,
                    "outcome": run.known_wrong_outcome.value,
                    "failed_tests": list(run.failed_tests),
                }
                for run in self.known_wrong
            ],
            "summary": {
                "rejected": self.rejected_count,
                "total": self.total_known_wrong,
                "score": self.score,
                "has_errors": self.has_errors,
                "sufficient": self.sufficient,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def render_text(self) -> str:
        baseline_status = "PASS" if self.baseline_valid else "INVALID"
        lines = [
            "tlaw property-strength audit",
            f"Baseline {self.baseline.name}: {baseline_status}",
            "Known-wrong implementations:",
        ]
        for run in self.known_wrong:
            outcome = run.known_wrong_outcome
            label = {
                KnownWrongOutcome.REJECTED: "REJECTED",
                KnownWrongOutcome.SURVIVED: "SURVIVED",
                KnownWrongOutcome.ERROR: "ERROR",
            }[outcome]
            detail = ""
            if run.failed_tests:
                detail = f" ({', '.join(run.failed_tests)})"
            lines.append(f"- {run.name}: {label}{detail}")

        lines.append(
            f"Strength: {self.rejected_count}/{self.total_known_wrong} "
            f"({self.score:.1%})"
        )
        if self.has_errors:
            verdict = "INVALID EVIDENCE"
        elif self.sufficient:
            verdict = "SUFFICIENT FOR DECLARED KNOWN-WRONG SET"
        else:
            verdict = "INSUFFICIENT"
        lines.append(f"Verdict: {verdict}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render_text()


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _string_list(value: object, *, field: str, required: bool) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ManifestError(f"{field} must be a list of non-empty strings")
    if required and not value:
        raise ManifestError(f"{field} must contain at least one entry")
    return tuple(value)


def _candidate(value: object, *, field: str, require_breaks: bool) -> Candidate:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be a table")
    breaks_value = value.get("breaks")
    if require_breaks:
        breaks = _string(breaks_value, field=f"{field}.breaks")
    elif breaks_value is None:
        breaks = None
    else:
        breaks = _string(breaks_value, field=f"{field}.breaks")
    return Candidate(
        name=_string(value.get("name"), field=f"{field}.name"),
        target=_string(value.get("target"), field=f"{field}.target"),
        breaks=breaks,
    )


def load_manifest(path: str | Path) -> AuditManifest:
    manifest_path = Path(path).resolve()
    try:
        with manifest_path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ManifestError(f"cannot read {manifest_path}: {error}") from error

    if data.get("version") != 1:
        raise ManifestError("version must be 1")
    tests = _string_list(data.get("tests"), field="tests", required=True)
    pytest_args = _string_list(
        data.get("pytest_args"), field="pytest_args", required=False
    )
    timeout = data.get("timeout_seconds", 30.0)
    if (
        not isinstance(timeout, int | float)
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        raise ManifestError("timeout_seconds must be a positive number")

    baseline = _candidate(data.get("baseline"), field="baseline", require_breaks=False)
    known_wrong_data = data.get("known_wrong")
    if not isinstance(known_wrong_data, list) or not known_wrong_data:
        raise ManifestError(
            "manifest must declare at least one known-wrong implementation"
        )
    known_wrong = tuple(
        _candidate(item, field=f"known_wrong[{index}]", require_breaks=True)
        for index, item in enumerate(known_wrong_data)
    )
    return _validated_manifest(
        path=manifest_path,
        tests=tests,
        pytest_args=pytest_args,
        timeout_seconds=float(timeout),
        baseline=baseline,
        known_wrong=known_wrong,
    )


def _validated_manifest(
    *,
    path: Path,
    tests: tuple[str, ...],
    pytest_args: tuple[str, ...],
    timeout_seconds: float,
    baseline: Candidate,
    known_wrong: tuple[Candidate, ...],
) -> AuditManifest:
    if not tests or not all(isinstance(test, str) and test for test in tests):
        raise ManifestError("tests must contain at least one path")
    if not all(isinstance(argument, str) and argument for argument in pytest_args):
        raise ManifestError("pytest_args must contain only non-empty strings")
    if (
        not isinstance(timeout_seconds, int | float)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ManifestError("timeout_seconds must be a positive number")
    if not known_wrong:
        raise ManifestError(
            "manifest must declare at least one known-wrong implementation"
        )

    candidates = (baseline, *known_wrong)
    for candidate in candidates:
        _string(candidate.name, field="candidate.name")
        _string(candidate.target, field=f"candidate {candidate.name}.target")
    for candidate in known_wrong:
        _string(candidate.breaks, field=f"candidate {candidate.name}.breaks")

    names = [candidate.name for candidate in candidates]
    if len(names) != len(set(names)):
        raise ManifestError("candidate names must be unique")
    for test in tests:
        test_path = path.parent / test.split("::", maxsplit=1)[0]
        if not test_path.exists():
            raise ManifestError(f"test path does not exist: {test}")

    return AuditManifest(
        path=path,
        tests=tests,
        pytest_args=pytest_args,
        timeout_seconds=float(timeout_seconds),
        baseline=baseline,
        known_wrong=known_wrong,
    )


def load_audit_spec(path: str | Path) -> AuditManifest:
    """Load either a code-first laws.py file or a legacy TOML manifest."""
    spec_path = Path(path).resolve()
    if spec_path.suffix == ".py":
        from .book import load_lawbook

        return load_lawbook(spec_path).to_manifest()
    if spec_path.suffix == ".toml":
        return load_manifest(spec_path)
    raise ManifestError("audit specification must be a .py or .toml file")


def _failed_tests(output: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_FAILED_TEST_RE.findall(output)))


def _run_candidate(manifest: AuditManifest, candidate: Candidate) -> RunResult:
    command = (
        sys.executable,
        "-m",
        "pytest",
        *manifest.pytest_args,
        *manifest.tests,
    )
    environment = os.environ.copy()
    environment[TARGET_ENV] = candidate.target
    environment.setdefault("PYTHONHASHSEED", "0")
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=manifest.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=manifest.timeout_seconds,
            check=False,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
        )
        stderr = (
            error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        )
        output = (stdout or "") + (stderr or "") + "\ntlaw: test run timed out."
        exit_code = 124

    return RunResult(
        name=candidate.name,
        target=candidate.target,
        exit_code=exit_code,
        failed_tests=_failed_tests(output),
        output=output,
        command=command,
    )


def run_audit(manifest: AuditManifest) -> AuditResult:
    baseline = _run_candidate(manifest, manifest.baseline)
    if baseline.exit_code == 0:
        known_wrong = tuple(
            _run_candidate(manifest, candidate) for candidate in manifest.known_wrong
        )
    else:
        known_wrong = tuple(
            RunResult(
                name=candidate.name,
                target=candidate.target,
                exit_code=3,
                output="Not run because the baseline test run was invalid.",
            )
            for candidate in manifest.known_wrong
        )
    return AuditResult(
        manifest=str(manifest.path),
        baseline=baseline,
        known_wrong=known_wrong,
    )


def audit_manifest(path: str | Path) -> AuditResult:
    return run_audit(load_audit_spec(path))
