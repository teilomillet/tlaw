"""Replay can resolve a checkout relative to the retained report itself."""

from __future__ import annotations

from pathlib import Path

import tlaw


FAILING_LAW = """\
import tlaw


@tlaw.law
def integers_stay_below_ten(value: int):
    return value < 10
"""


def test_report_relative_law_replays_from_an_unrelated_working_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    law_file = checkout / "laws.py"
    law_file.write_text(FAILING_LAW, encoding="utf-8")

    monkeypatch.chdir(checkout)
    report = checkout / "failure.json"
    report.write_text(
        tlaw.check("laws.py", search=True, seed=20260901).to_json(),
        encoding="utf-8",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    replayed = tlaw.replay(report)

    assert replayed.exit_code == 0
    assert "retained counterexample reproduced" in replayed.output
