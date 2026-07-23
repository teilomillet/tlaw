"""Enforce that the vocabulary ladder cannot silently drift from the code.

The ``tlaw`` module docstring teaches *which verb to reach for when* — the
escalation ladder. A ladder decays the moment a new public name is added to
``__all__`` without being placed on it. These tests make that decay a gate
failure instead of a slow rot: every export must be a ladder rung or an
explicitly exempted non-rung, and every exemption must still exist.
"""

from __future__ import annotations

import re

import tlaw


# Public names that are deliberately NOT ladder rungs. The ladder teaches
# which verb to reach for; these are the values those verbs return, the
# errors they raise, and modes of a command that already has a rung. Adding
# a genuinely new *verb* must not land here — it must go on the ladder.
NOT_LADDER_RUNGS = {
    # Result and value types — what a rung returns, not a rung itself.
    "Comparison",
    "Refusal",
    "Outcome",
    "Exclusion",
    "Equation",
    "Path",
    "Domain",
    "DependentDomain",
    "System",
    "AuditResult",
    "CheckResult",
    "FolderCheckResult",
    "MutationResult",
    "MutantRun",
    "Mutation",
    "MutationOutcome",
    "CanaryRun",
    "KnownWrongOutcome",
    "LawChange",
    "LawDiffResult",
    # Errors.
    "ManifestError",
    "EnforcementError",
    # Modes and helpers of a command that already has a rung: check_folder
    # and replay are shapes of `check`; discover_law_files is its library
    # helper.
    "check_folder",
    "discover_law_files",
    "replay",
}


def _mentions(name: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def test_every_public_name_is_a_ladder_rung_or_explicitly_exempt() -> None:
    ladder = tlaw.__doc__ or ""
    for name in tlaw.__all__:
        if name in NOT_LADDER_RUNGS:
            continue
        assert _mentions(name, ladder), (
            f"{name!r} is public vocabulary but is missing from the ladder in "
            "tlaw/__init__.py's module docstring. Put it on the ladder as a "
            "rung, or add it to NOT_LADDER_RUNGS with a reason if it is a "
            "type, error, or mode rather than a verb to reach for."
        )


def test_no_exemption_names_a_dropped_export() -> None:
    exported = set(tlaw.__all__)
    for name in NOT_LADDER_RUNGS:
        assert name in exported, (
            f"{name!r} is exempted from the ladder but is no longer exported; "
            "remove it from NOT_LADDER_RUNGS so the exemption list stays honest."
        )


def test_the_ladder_and_the_exemptions_together_cover_the_surface() -> None:
    # Every export is exactly one of: a ladder rung, or a declared non-rung.
    # No export may be silently unaccounted for in either place.
    ladder = tlaw.__doc__ or ""
    unaccounted = [
        name
        for name in tlaw.__all__
        if name not in NOT_LADDER_RUNGS and not _mentions(name, ladder)
    ]
    assert not unaccounted, (
        "these exports are neither a ladder rung nor a declared non-rung: "
        f"{unaccounted}"
    )
