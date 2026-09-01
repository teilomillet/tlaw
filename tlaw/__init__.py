"""Write, check, and audit executable laws.

The vocabulary is a ladder. Start at the bottom; climb a rung only when the
one below cannot say what you mean. Most laws never leave the first rung.

Stating a law — what the function returns:

1. ``@tlaw.law`` with ``return a == b`` — a plain boolean. The whole first
   law is this.
2. ``same(a, b, within=, relative=)`` — when ``==`` is the wrong test:
   floats, arrays, nested values, or a tolerance. Keeps both sides and the
   first difference on failure.
3. ``raises(Exc, f, ...)`` — when the claim is that a *call raises*.
   ``returns(Type, f, ...)`` — when the claim is that a call *returns* an
   error-value branch (a Result/``Err``) instead of raising.
4. ``excludes(text, *forbidden)`` — when the claim is that something must
   *not* appear: a leaked secret, hostname, or traceback.
5. ``raises(...).and_then(observe)`` — when one call carries *two* claims
   (it refused, *and* the surfaced message is redacted).
   ``same(...).and_then(next_claim)`` — when equivalence is only the first
   claim (the results agree, *and* both runs did the expected work).

Generating inputs — what the annotation says:

1. ``values: list[int]`` — a plain type. Start here.
2. ``number(lo, hi)`` / ``integer(lo, hi)`` / ``one_of(...)`` — when the
   plain type is broader than the real domain.
3. ``list_of(...)`` — a list of a domain, with visible size bounds.
4. ``recursive(...)`` — finite recursively nested values with a visible leaf
   budget.
5. ``depends(...)`` / ``record(...)`` — when inputs are *related*: one bounds
   another, or a record's fields must agree.

When behavior mutates state, describe two paths from fresh starts instead:
``system``, ``.call``/``.then``, ``.same_as``.

Getting evidence — a separate axis, ordered by how hard the laws are tried:

1. ``tlaw check`` — do the laws hold against the code now?
2. ``tlaw mutate`` — do the laws actually *constrain* the code? (adversarial)
3. ``tlaw diff`` — did a commit change what a law *means*?
4. ``tlaw audit`` — do the laws reject declared-wrong implementations?

Each public callable has its own docstring with the why; this is the map of
when to reach for which.
"""

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
    recursive,
)
from .exclusion import Exclusion, excludes
from .law_diff import LawChange, LawDiffResult, diff
from .outcome import Outcome, returns
from .refusal import Refusal, raises
from .mutation import (
    CanaryRun,
    KillKind,
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
    "KillKind",
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
    "recursive",
    "replay",
    "returns",
    "same",
    "system",
]
