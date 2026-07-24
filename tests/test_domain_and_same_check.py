"""End-to-end probe: the retrain-shaped law through `tlaw check`.

This is the probe doc's syntax candidate as a real laws file: domains as
module-level names used in annotations, `tlaw.same` with a visible
tolerance, run through the same subprocess path a user runs. It pins the
whole loop — collection, generation from domains, deterministic runs,
`--explain` naming the domains, and a failure that shows both sides.
"""

from __future__ import annotations

from pathlib import Path

import tlaw


PASSING_LAWS = """\
import tlaw

from implementation import advantages


Reward = tlaw.number(0, 1)
Rewards = tlaw.list_of(Reward, min_size=1, max_size=8)


@tlaw.law
def advantages_are_centered(rewards: Rewards):
    return tlaw.same(sum(advantages(rewards)), 0.0, within=1e-9)


@tlaw.law
def one_advantage_per_reward(rewards: Rewards):
    return len(advantages(rewards)) == len(rewards)
"""

IMPLEMENTATION = """\
def advantages(rewards):
    mean = sum(rewards) / len(rewards)
    return [reward - mean for reward in rewards]
"""

FAILING_LAWS = """\
import tlaw


Reward = tlaw.number(0, 1)
Rewards = tlaw.list_of(Reward, min_size=1, max_size=4)


@tlaw.law
def rewards_equal_their_doubles(rewards: Rewards):
    return tlaw.same([reward * 2 for reward in rewards], rewards, within=1e-6)
"""

CHAINED_FAILING_LAWS = """\
import tlaw


Values = tlaw.list_of(int, min_size=1, max_size=4)


@tlaw.law
def matching_no_ops_do_not_count_as_work(values: Values):
    refactored = []
    baseline = []
    return tlaw.same(refactored, baseline).and_then(
        lambda: tlaw.same(
            (len(refactored), len(baseline)),
            (len(values), len(values)),
        )
    )
"""


def test_domain_laws_with_tolerant_comparison_pass_through_check(
    tmp_path: Path,
) -> None:
    (tmp_path / "implementation.py").write_text(IMPLEMENTATION, encoding="utf-8")
    (tmp_path / "laws.py").write_text(PASSING_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py")

    assert result.passed is True
    assert "2 passed" in result.output


def test_explain_names_the_domains_not_hypothesis_internals(tmp_path: Path) -> None:
    (tmp_path / "implementation.py").write_text(IMPLEMENTATION, encoding="utf-8")
    (tmp_path / "laws.py").write_text(PASSING_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py", explain=True)

    assert result.passed is True
    assert "rewards: list_of(number(0, 1), min_size=1, max_size=8)" in result.output
    assert "deterministic, no example database" in result.output


EXPLICIT_FORM_LAWS = """\
from hypothesis import strategies as st

import tlaw


Gpus = tlaw.integer(1, 8)


@tlaw.law("divisors never exceed their number", gpus=Gpus, extra=st.integers(0, 3))
def divisors_never_exceed(gpus, extra):
    return all(size <= gpus + extra for size in range(1, gpus + 1) if gpus % size == 0)
"""


def test_explain_states_the_explicit_forms_posture(tmp_path: Path) -> None:
    # kvfit probe bottleneck 2: the explicit form follows ordinary
    # Hypothesis settings and skips the mutation guard. That posture must
    # be stated in --explain, not silently different from the bare form.
    (tmp_path / "laws.py").write_text(EXPLICIT_FORM_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py", explain=True)

    assert result.passed is True
    assert "gpus: integer(1, 8)" in result.output
    assert "Hypothesis settings decide examples and randomization" in result.output
    assert "no input-mutation guard" in result.output


def test_a_failing_same_law_shows_both_sides_and_the_shrunk_input(
    tmp_path: Path,
) -> None:
    (tmp_path / "laws.py").write_text(FAILING_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py")

    assert result.passed is False
    assert "law failed: rewards equal their doubles" in result.output
    assert "first difference at value[0]" in result.output
    assert "absolute difference" in result.output
    assert "Failing test case" in result.output


def test_a_chained_comparison_reports_the_second_claim_not_false(
    tmp_path: Path,
) -> None:
    (tmp_path / "laws.py").write_text(CHAINED_FAILING_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py")

    assert result.passed is False
    assert "law failed: matching no ops do not count as work" in result.output
    assert "first difference at value[0]" in result.output
    assert "left:  0" in result.output
    assert "right: 1" in result.output
    assert "law returned False" not in result.output


ALIAS_EXPLAIN_LAWS = """\
from typing import Annotated

import tlaw


type Reward = Annotated[float, tlaw.number(0, 1)]
type Count = Annotated[int, tlaw.integer(1, 8)]


@tlaw.law
def bounded(rewards: list[Reward], count: Count):
    return all(0 <= r <= 1 for r in rewards) and 1 <= count <= 8
"""


def test_explain_expands_aliased_domain_bounds(tmp_path: Path) -> None:
    # A PEP 695 alias hid its domain behind the alias name in --explain;
    # it is now expanded to the generated domain.
    (tmp_path / "laws.py").write_text(ALIAS_EXPLAIN_LAWS, encoding="utf-8")

    result = tlaw.check(tmp_path / "laws.py", explain=True)

    assert result.passed is True
    assert "rewards: list[number(0, 1)]" in result.output
    assert "count: integer(1, 8)" in result.output
    assert "list[Reward]" not in result.output
