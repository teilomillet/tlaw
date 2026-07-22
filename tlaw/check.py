"""Run human-authored law files against their current implementations."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .audit import TARGET_ENV, ManifestError


@dataclass(frozen=True)
class CheckResult:
    path: str
    exit_code: int
    output: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def check(
    path: str | Path,
    *,
    timeout_seconds: float = 60.0,
    explain: bool = False,
) -> CheckResult:
    """Run one laws.py file with pytest and no audit substitution."""
    law_path = Path(path).resolve()
    if law_path.suffix != ".py" or not law_path.is_file():
        raise ManifestError(f"law file does not exist or is not Python: {law_path}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if explain:
        environment["TLAW_EXPLAIN"] = "1"
    else:
        environment.pop("TLAW_EXPLAIN", None)
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tlaw.pytest_plugin",
        law_path.name,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=law_path.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
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
        output = (stdout or "") + (stderr or "") + "\ntlaw: check timed out.\n"
        exit_code = 124

    return CheckResult(path=str(law_path), exit_code=exit_code, output=output)
