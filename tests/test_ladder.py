"""Enforce that the vocabulary ladder cannot silently drift from the code.

The ``tlaw`` module docstring teaches *which verb to reach for when* — the
escalation ladder. A ladder decays the moment a new public name is added to
``__all__`` without being placed on it. These tests make that decay a gate
failure instead of a slow rot: every export must be a ladder rung or an
explicitly exempted non-rung, and every exemption must still exist.
"""

from __future__ import annotations

import tlaw


LADDER_TOKENS = {
    "audit": "``tlaw audit``",
    "check": "``tlaw check``",
    "depends": "``depends(",
    "diff": "``tlaw diff``",
    "excludes": "``excludes(",
    "integer": "``integer(",
    "law": "``@tlaw.law``",
    "list_of": "``list_of(",
    "mutate": "``tlaw mutate``",
    "number": "``number(",
    "one_of": "``one_of(",
    "raises": "``raises(",
    "record": "``record(",
    "recursive": "``recursive(",
    "returns": "``returns(",
    "same": "``same(",
    "system": "``system``",
}

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
    "CampaignPlan",
    "MutantRun",
    "Mutation",
    "MutationOutcome",
    "KillKind",
    "CanaryRun",
    "KnownWrongOutcome",
    "LawChange",
    "LawDiffResult",
    # Audit authoring: ``tlaw audit`` runs a Lawbook; the book is the
    # declaration it reads, not a separate rung.
    "Lawbook",
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


def test_every_public_name_is_a_ladder_rung_or_explicitly_exempt() -> None:
    public = set(tlaw.__all__)
    rungs = set(LADDER_TOKENS)

    assert rungs.isdisjoint(NOT_LADDER_RUNGS)
    assert rungs | NOT_LADDER_RUNGS == public, (
        "every public name must be exactly one of a ladder rung or an explicit "
        f"non-rung; missing={public - rungs - NOT_LADDER_RUNGS}, "
        f"stale={(rungs | NOT_LADDER_RUNGS) - public}"
    )


def test_every_ladder_rung_has_an_explicit_api_token() -> None:
    ladder = tlaw.__doc__ or ""
    for name, token in LADDER_TOKENS.items():
        assert token in ladder, (
            f"{name!r} is classified as a ladder rung but its explicit API "
            f"token {token!r} is missing from tlaw's module docstring"
        )
