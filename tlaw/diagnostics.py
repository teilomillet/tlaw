"""Explanations of execution outcomes, separate from law evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal, get_args


DIAGNOSTICS_ENV = "TLAW_DIAGNOSTICS"
DiagnosticPhase = Literal["collect", "setup", "call", "teardown", "run", "receipt"]


@dataclass(frozen=True)
class RunDiagnostic:
    """One finalized outcome; its detail preserves formatted exception causes.

    Diagnostics explain a run. They neither establish a law refutation nor
    supply retained inputs, attribution or confirmation for a mutation kill.
    """

    nodeid: str
    phase: DiagnosticPhase
    exception_type: str | None
    message: str
    detail: str = ""
    identity: str | None = None

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        nodeid: str,
        phase: DiagnosticPhase,
        detail: str = "",
        identity: str | None = None,
    ) -> RunDiagnostic:
        try:
            message = str(error)
        except Exception:
            message = "<exception message could not be rendered>"
        error_type = type(error)
        return cls(
            nodeid=nodeid,
            phase=phase,
            exception_type=f"{error_type.__module__}.{error_type.__qualname__}",
            message=message,
            detail=detail,
            identity=identity,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_text(self) -> str:
        kind = f"{self.exception_type}: " if self.exception_type else ""
        message = self.message.replace("\n", "\n    ")
        return f"diagnostic [{self.phase}] {self.nodeid or 'pytest'}: {kind}{message}"


_FIELDS = {field.name for field in fields(RunDiagnostic)}


def read_diagnostics(
    path: Path, *, missing_ok: bool = False
) -> tuple[RunDiagnostic, ...]:
    """Read a diagnostic receipt without changing the execution verdict.

    A missing or malformed receipt is itself a diagnostic, never a substitute
    for the independent obligation receipt. A terminated child may not reach
    pytest's session-finish hook; its process-level timeout explains that absence.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("diagnostic receipt must contain a list")
        for entry in payload:
            if (
                not isinstance(entry, dict)
                or set(entry) != _FIELDS
                or not isinstance(entry["nodeid"], str)
                or entry["phase"] not in get_args(DiagnosticPhase)
                or not isinstance(entry["message"], str)
                or not isinstance(entry["detail"], str)
                or (
                    entry["exception_type"] is not None
                    and not isinstance(entry["exception_type"], str)
                )
                or (
                    entry["identity"] is not None
                    and (
                        not isinstance(entry["identity"], str)
                        or "::" not in entry["identity"]
                    )
                )
            ):
                raise ValueError("diagnostic receipt contains an invalid entry")
        return tuple(RunDiagnostic(**entry) for entry in payload)
    except FileNotFoundError as error:
        if missing_ok:
            return ()
        return (RunDiagnostic.from_exception(error, nodeid=path.name, phase="receipt"),)
    except (OSError, ValueError, RecursionError) as error:
        return (RunDiagnostic.from_exception(error, nodeid=path.name, phase="receipt"),)
