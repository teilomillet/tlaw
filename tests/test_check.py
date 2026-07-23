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


def test_check_ignores_the_target_projects_pytest_addopts(tmp_path: Path) -> None:
    """A probed project's addopts must not decide how tlaw runs its laws.

    Probing a codebase must not require editing that codebase. A real target
    (the anef probe) configures ``addopts = ["-n", "6"]`` for pytest-xdist,
    which tlaw deliberately does not autoload, so inheriting those flags made
    every check a pytest usage error instead of a law verdict.
    """
    (tmp_path / "pyproject.toml").write_text(
        """\
[tool.pytest.ini_options]
addopts = ["-n", "6", "--dist", "loadgroup"]
""",
        encoding="utf-8",
    )
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def adding_zero_changes_nothing(value: int):
    return value + 0 == value
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0
    assert "unrecognized arguments" not in result.output
    assert "1 passed" in result.output


def _unretainable_law(tmp_path: Path) -> Path:
    """A law whose counterexample is a type the evidence encoder cannot hold."""
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
from dataclasses import dataclass

import tlaw


@dataclass(frozen=True)
class Point:
    size: int


Points = tlaw.record(Point, size=tlaw.integer(0, 5))


@tlaw.law
def every_point_is_small(point: Points):
    return point.size < 3
""",
        encoding="utf-8",
    )
    return law_file


def test_a_refuted_law_is_reported_as_failed_even_when_it_cannot_be_retained(
    tmp_path: Path,
) -> None:
    """Two different facts must not overwrite each other.

    tlaw established that the law was refuted. It separately could not encode
    the counterexample for replay. Reporting the run as invalid would erase
    the first fact, and a CI gate would read a real defect as a broken
    harness.
    """
    result = tlaw.check(_unretainable_law(tmp_path))

    assert result.exit_code == 1
    assert result.report["summary"]["status"] == "failed"
    assert result.report["summary"]["retained"] is False
    assert "the law above was refuted" in result.output
    assert "cannot be retained for replay" in result.output


def test_a_retainable_failure_reports_both_facts_positively(tmp_path: Path) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw

Shapes = tlaw.record(size=tlaw.integer(0, 5))


@tlaw.law
def every_shape_is_small(shape: Shapes):
    return shape["size"] < 3
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 1
    assert result.report["summary"]["status"] == "failed"
    assert result.report["summary"]["retained"] is True


def test_a_harness_failure_is_still_invalid_not_a_law_verdict(tmp_path: Path) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text("import nonexistent_module_for_tlaw\n", encoding="utf-8")

    result = tlaw.check(law_file)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
