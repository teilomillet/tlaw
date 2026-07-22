from __future__ import annotations

from pathlib import Path

import pytest
import tlaw


ROOT = Path(__file__).resolve().parents[1]


def test_check_runs_a_human_law_file_without_audit_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TLAW_TARGET", "missing:Target")

    result = tlaw.check(ROOT / "examples" / "codec" / "laws.py")

    assert result.passed is True
    assert result.exit_code == 0
    assert "1 passed" in result.output


def test_check_reports_a_generated_counterexample_for_a_bare_law(
    tmp_path: Path,
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def adding_one_changes_nothing(value: int):
    return value + 1 == value
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.passed is False
    assert "law failed: adding one changes nothing" in result.output
    assert "Failing test case" in result.output
    assert "value=0" in result.output


def test_check_explains_inferred_generation() -> None:
    result = tlaw.check(
        ROOT / "examples" / "list_tools" / "laws.py",
        explain=True,
    )

    assert result.passed is True
    assert "tlaw generation (Hypothesis" in result.output
    assert "reversing twice returns the original" in result.output
    assert "values: list[int]" in result.output
    assert "up to 100 examples; deterministic, no example database" in result.output
    assert "pytest plugins: third-party autoload disabled" in result.output


def test_bare_law_replays_the_same_minimized_failure(tmp_path: Path) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def integers_are_less_than_ten(value: int):
    return value < 10
""",
        encoding="utf-8",
    )

    first = tlaw.check(law_file)
    second = tlaw.check(law_file)

    assert first.passed is False
    assert second.passed is False
    assert "value=10" in first.output
    assert "value=10" in second.output
