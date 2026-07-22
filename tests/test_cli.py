"""Laws for the command-line exit contract.

Exit codes are the interface CI pipelines script against, so they are
pinned here at the argparse boundary — not only at the result-object
level: 0 means the evidence holds, 1 means a law failed or an unexplained
survivor remains, 2 means the evidence itself is broken.
"""

from __future__ import annotations

import json
from pathlib import Path

from tlaw.cli import main


PASSING_LAW = """\
import tlaw


@tlaw.law
def zero_stays_zero(value: int):
    return value * 0 == 0
"""

FAILING_LAW = """\
import tlaw


@tlaw.law
def integers_stay_small(value: int):
    return value < 10
"""

SIGN_IMPLEMENTATION = """\
def sign(value):
    if value < 0:
        return -1
    return 1
"""

STRONG_LAWS = """\
import tlaw

from implementation import sign


@tlaw.law
def sign_matches_a_reference(value: int):
    return sign(value) == (-1 if value < 0 else 1)
"""

WEAK_LAWS = """\
import tlaw

from implementation import double_all


@tlaw.law
def double_all_keeps_length(values: list[int]):
    return len(double_all(values)) == len(values)
"""

DOUBLE_IMPLEMENTATION = """\
def double_all(values):
    return [value + value for value in values]
"""

UNWIRED_LAWS = """\
import tlaw


@tlaw.law
def a_truth_about_nothing(value: int):
    return value == value
"""


def test_check_exits_zero_on_pass_and_one_on_a_law_failure(tmp_path: Path) -> None:
    passing = tmp_path / "passing.py"
    passing.write_text(PASSING_LAW, encoding="utf-8")
    failing = tmp_path / "failing.py"
    failing.write_text(FAILING_LAW, encoding="utf-8")

    assert main(["check", str(passing)]) == 0
    assert main(["check", str(failing)]) == 1


def test_check_exits_two_when_the_law_file_does_not_exist(tmp_path: Path) -> None:
    assert main(["check", str(tmp_path / "missing.py")]) == 2


def test_mutate_exits_zero_when_every_mutant_is_killed(tmp_path: Path) -> None:
    (tmp_path / "implementation.py").write_text(SIGN_IMPLEMENTATION, encoding="utf-8")
    (tmp_path / "laws.py").write_text(STRONG_LAWS, encoding="utf-8")
    report = tmp_path / "report.json"

    exit_code = main(
        [
            "mutate",
            str(tmp_path / "laws.py"),
            str(tmp_path / "implementation.py"),
            "--json",
            str(report),
        ]
    )

    assert exit_code == 0
    recorded = json.loads(report.read_text(encoding="utf-8"))
    assert recorded["schema"] == "tlaw.mutation/v1"
    assert recorded["summary"]["survived"] == 0
    assert recorded["canary"]["wired"] is True


def test_mutate_exits_one_when_an_unexplained_survivor_remains(
    tmp_path: Path,
) -> None:
    (tmp_path / "implementation.py").write_text(DOUBLE_IMPLEMENTATION, encoding="utf-8")
    (tmp_path / "laws.py").write_text(WEAK_LAWS, encoding="utf-8")

    assert (
        main(["mutate", str(tmp_path / "laws.py"), str(tmp_path / "implementation.py")])
        == 1
    )


def test_mutate_exits_two_when_the_canary_survives(tmp_path: Path) -> None:
    # Laws that never import the implementation: unwired evidence must be
    # a loud 2, never a page of survivors.
    (tmp_path / "implementation.py").write_text(SIGN_IMPLEMENTATION, encoding="utf-8")
    (tmp_path / "laws.py").write_text(UNWIRED_LAWS, encoding="utf-8")

    assert (
        main(["mutate", str(tmp_path / "laws.py"), str(tmp_path / "implementation.py")])
        == 2
    )
