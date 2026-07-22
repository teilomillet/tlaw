from __future__ import annotations

from pathlib import Path

from tlaw.audit import KnownWrongOutcome, audit_manifest


ROOT = Path(__file__).resolve().parents[1]
WALLET = ROOT / "examples" / "wallet"


def test_weak_wallet_laws_reveal_the_missing_identity_property() -> None:
    result = audit_manifest(WALLET / "tlaw-weak.toml")

    assert result.baseline_valid is True
    assert result.rejected_count == 3
    assert result.total_known_wrong == 4
    assert result.score == 0.75
    assert result.sufficient is False
    assert result.outcome_for("zero-transfer-fee") is KnownWrongOutcome.SURVIVED


def test_strong_wallet_laws_reject_every_known_wrong_implementation() -> None:
    result = audit_manifest(WALLET / "audit.py")

    assert result.baseline_valid is True
    assert result.rejected_count == 4
    assert result.total_known_wrong == 4
    assert result.score == 1.0
    assert result.sufficient is True


def test_wallet_audit_report_keeps_failed_property_names() -> None:
    result = audit_manifest(WALLET / "audit.py")
    minting = next(run for run in result.known_wrong if run.name == "minting-transfer")

    assert any("transfer_is_exact" in test_name for test_name in minting.failed_tests)
