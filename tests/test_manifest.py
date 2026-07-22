from __future__ import annotations

from pathlib import Path

import pytest

from tlaw.audit import ManifestError, load_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_wallet_manifest_preserves_human_names_and_intended_mistakes() -> None:
    manifest = load_manifest(ROOT / "examples" / "wallet" / "tlaw.toml")

    assert manifest.baseline.name == "correct"
    assert [candidate.name for candidate in manifest.known_wrong] == [
        "double-deposit",
        "negative-withdrawal",
        "minting-transfer",
        "zero-transfer-fee",
    ]
    assert all(candidate.breaks for candidate in manifest.known_wrong)


def test_manifest_rejects_duplicate_candidate_names(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tlaw.toml"
    manifest_path.write_text(
        """
version = 1
tests = ["test_laws.py"]

[baseline]
name = "same"
target = "wallets:Reference"

[[known_wrong]]
name = "same"
target = "wallets:Wrong"
breaks = "It is deliberately wrong."
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="unique"):
        load_manifest(manifest_path)


def test_manifest_requires_at_least_one_known_wrong_example(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tlaw.toml"
    manifest_path.write_text(
        """
version = 1
tests = ["test_laws.py"]

[baseline]
name = "correct"
target = "wallets:Reference"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="known-wrong"):
        load_manifest(manifest_path)
