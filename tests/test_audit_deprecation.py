"""Probes for the legacy TOML audit path's deprecation warning.

TOML manifests keep working in this release, but loading one announces that
the supported audit format is the Python lawbook. The code-first path must stay
silent so the deprecation points only at the legacy format.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from tlaw.audit import load_audit_spec


ROOT = Path(__file__).resolve().parents[1]

MANIFEST = """\
version = 1
tests = ["test_laws.py"]

[baseline]
name = "reference"
target = "implementations:Reference"

[[known_wrong]]
name = "wrong"
target = "implementations:Wrong"
breaks = "It is deliberately wrong."
"""


def _toml_manifest(tmp_path: Path) -> Path:
    (tmp_path / "test_laws.py").write_text(
        "def test_laws():\n    assert True\n", encoding="utf-8"
    )
    manifest = tmp_path / "tlaw.toml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    return manifest


def test_loading_a_toml_manifest_warns_that_it_is_deprecated(
    tmp_path: Path,
) -> None:
    manifest = _toml_manifest(tmp_path)

    with pytest.warns(DeprecationWarning, match="TOML audit manifests are deprecated"):
        loaded = load_audit_spec(manifest)

    assert loaded.path == manifest.resolve()
    assert loaded.baseline.name == "reference"


def test_a_python_lawbook_audit_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = load_audit_spec(ROOT / "selflaws" / "audit.py")

    assert loaded.baseline.name
    deprecations = [
        warning
        for warning in caught
        if issubclass(warning.category, DeprecationWarning)
    ]
    assert deprecations == []


def test_the_cli_makes_the_toml_deprecation_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tlaw.cli import main

    exit_code = main(["audit", str(ROOT / "examples" / "wallet" / "tlaw.toml")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "TOML audit manifests are deprecated" in captured.err
