"""End-to-end proof that mutation evidence cannot succeed vacuously."""

from __future__ import annotations

import json
from pathlib import Path

from tlaw.cli import main


IMPLEMENTATION_WITHOUT_SUPPORTED_SITES = """\
def identity(value):
    return value
"""

LAWS = """\
import tlaw

from implementation import identity


@tlaw.law
def identity_returns_its_input(value: int):
    return identity(value) == value
"""


def test_zero_generated_mutations_are_invalid_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    implementation = tmp_path / "implementation.py"
    implementation.write_text(
        IMPLEMENTATION_WITHOUT_SUPPORTED_SITES,
        encoding="utf-8",
    )
    laws = tmp_path / "laws.py"
    laws.write_text(LAWS, encoding="utf-8")
    report = tmp_path / "report.json"

    exit_code = main(["mutate", str(laws), str(implementation), "--json", str(report)])

    assert exit_code == 2
    recorded = json.loads(report.read_text(encoding="utf-8"))
    assert recorded["mutations"] == []
    assert recorded["canary"] is None
    assert recorded["summary"]["generated"] == 0
    assert recorded["summary"]["has_errors"] is True
    assert recorded["summary"]["statement"] == (
        "No supported mutations were generated; no mutation evidence exists."
    )
    assert "INVALID EVIDENCE (no supported mutation sites)" in capsys.readouterr().out
