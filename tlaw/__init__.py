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
from .check import (
    CheckResult,
    FolderCheckResult,
    check,
    check_folder,
    discover_law_files,
    replay,
)
from .comparison import Comparison, same
from .domains import (
    DependentDomain,
    Domain,
    depends,
    integer,
    list_of,
    number,
    one_of,
    record,
)
from .exclusion import Exclusion, excludes
from .law_diff import LawChange, LawDiffResult, diff
from .outcome import Outcome, returns
from .refusal import Refusal, raises
from .mutation import (
    CanaryRun,
    Mutation,
    MutationOutcome,
    MutationResult,
    MutantRun,
    mutate,
)


def audit(specification: str | FilePath = "audit.py") -> AuditResult:
    """Run a Python law file or TOML manifest and return its evidence."""
    return audit_manifest(specification)


__all__ = [
    "AuditResult",
    "CanaryRun",
    "CheckResult",
    "Comparison",
    "DependentDomain",
    "Domain",
    "EnforcementError",
    "Equation",
    "Exclusion",
    "FolderCheckResult",
    "KnownWrongOutcome",
    "LawChange",
    "LawDiffResult",
    "ManifestError",
    "MutantRun",
    "Mutation",
    "MutationOutcome",
    "MutationResult",
    "Outcome",
    "Path",
    "Refusal",
    "System",
    "audit",
    "check",
    "check_folder",
    "depends",
    "diff",
    "discover_law_files",
    "excludes",
    "integer",
    "law",
    "list_of",
    "mutate",
    "number",
    "one_of",
    "raises",
    "record",
    "replay",
    "returns",
    "same",
    "system",
]
