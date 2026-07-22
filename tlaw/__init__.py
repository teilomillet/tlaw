"""Write, check, and audit executable laws."""

from __future__ import annotations

from pathlib import Path as FilePath

from .audit import (
    AuditResult,
    EnforcementError,
    KnownWrongOutcome,
    ManifestError,
    audit_manifest,
)
from .authoring import Equation, Path, System, law, system
from .check import CheckResult, check
from .comparison import Comparison, same
from .domains import (
    DependentDomain,
    Domain,
    depends,
    integer,
    list_of,
    number,
    one_of,
)
from .refusal import Refusal, raises


def audit(specification: str | FilePath = "audit.py") -> AuditResult:
    """Run a Python law file or TOML manifest and return its evidence."""
    return audit_manifest(specification)


__all__ = [
    "AuditResult",
    "CheckResult",
    "Comparison",
    "DependentDomain",
    "Domain",
    "EnforcementError",
    "Equation",
    "KnownWrongOutcome",
    "ManifestError",
    "Path",
    "Refusal",
    "System",
    "audit",
    "check",
    "depends",
    "integer",
    "law",
    "list_of",
    "number",
    "one_of",
    "raises",
    "same",
    "system",
]
