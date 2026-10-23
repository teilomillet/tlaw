from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import tlaw


ROOT = Path(__file__).resolve().parents[1]


def test_every_advertised_name_is_importable_and_declared() -> None:
    """A feature is not finished until it reaches the public surface.

    This catches the half-wired state directly: a helper implemented,
    tested, and documented, but never exported — or exported and then
    removed while `__all__` still advertises it.
    """
    for name in tlaw.__all__:
        assert hasattr(tlaw, name), f"tlaw.__all__ advertises missing {name!r}"

    assert tlaw.__all__ == sorted(tlaw.__all__)
    assert len(tlaw.__all__) == len(set(tlaw.__all__))


def test_the_authoring_vocabulary_is_present() -> None:
    # The names a first law needs, and the escalations documented in
    # docs/current-behavior.md. Adding a vocabulary word means adding it
    # here, so the public surface changes deliberately.
    for name in (
        "law",
        "system",
        "same",
        "raises",
        "number",
        "integer",
        "list_of",
        "one_of",
        "recursive",
        "depends",
        "check",
        "diff",
        "replay",
        "audit",
        "mutate",
        "Lawbook",
    ):
        assert callable(getattr(tlaw, name)), f"{name} must be callable"


def test_lawbook_is_exported_without_removing_its_documented_import_path() -> None:
    from tlaw.book import Lawbook as BookLawbook

    assert tlaw.Lawbook is BookLawbook
    assert "Lawbook" in tlaw.__all__


def test_internal_domain_provenance_is_not_a_public_constructor_argument() -> None:
    assert tuple(inspect.signature(tlaw.Domain).parameters) == (
        "description",
        "strategy",
        "generates",
    )


def test_import_tlaw_exposes_the_beginner_interface() -> None:
    result = tlaw.audit(ROOT / "examples" / "wallet" / "audit.py")

    assert isinstance(result, tlaw.AuditResult)
    assert result.sufficient is True
    assert str(result) == result.render_text()
    assert result.enforce() is result


def test_audit_defaults_to_audit_py(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT / "examples" / "wallet")

    assert tlaw.audit().sufficient is True
