"""Laws for `tlaw.returns`: the one spelling of a return-branch contract.

Born from the anef probe (issue #1, gap G2): `interpret` and `resolve`
return `Result[Value, Failure]` instead of raising, so every contract about
their failure path was a bare boolean whose failure output said only
`False` — it could not distinguish "returned the other branch" from
"raised instead".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

import tlaw
from tlaw import returns


@dataclass(frozen=True)
class Ok:
    value: Any


@dataclass(frozen=True)
class Err:
    error: Any


def _classify(value: int) -> Ok | Err:
    if value < 0:
        return Err("negative")
    return Ok(value * 2)


def test_the_expected_branch_is_a_truthy_outcome() -> None:
    outcome = returns(Err, _classify, -1)

    assert outcome.matched
    assert bool(outcome) is True
    assert outcome.explain() == "the call returned Err as required"


def test_a_subclass_counts_like_isinstance() -> None:
    @dataclass(frozen=True)
    class NotFound(Err):
        pass

    assert returns(Err, lambda: NotFound("gone")).matched


def test_the_other_branch_is_named_not_reduced_to_false() -> None:
    outcome = returns(Err, _classify, 3)

    assert not outcome.matched
    assert "expected Err, but the call returned Ok(value=6)" in outcome.explain()


def test_an_exception_is_named_as_the_failure_of_a_return_contract() -> None:
    def explode() -> None:
        raise TypeError("wrong shape")

    outcome = returns(Err, explode)

    assert not outcome.matched
    assert "expected Err, but the call raised TypeError: wrong shape" in (
        outcome.explain()
    )


def test_a_non_exception_exit_propagates_instead_of_becoming_evidence() -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        returns(Err, interrupt)


def test_an_async_target_is_awaited_so_the_branch_is_actually_chosen() -> None:
    """Calling an async def only builds a coroutine; no branch exists yet."""

    async def classify(value: int) -> Ok | Err:
        return _classify(value)

    outcome = returns(Err, classify, -1)

    assert outcome.matched
    assert outcome.awaited is True
    assert outcome.explain() == "the awaited call returned Err as required"


def test_an_async_target_reports_the_value_not_the_coroutine() -> None:
    async def classify(value: int) -> Ok | Err:
        return _classify(value)

    outcome = returns(Err, classify, 3)

    assert not outcome.matched
    assert "the awaited call returned Ok(value=6)" in outcome.explain()


def test_the_expected_branch_must_be_a_type() -> None:
    with pytest.raises(TypeError):
        returns("Err", _classify, -1)


def test_an_exception_type_is_refused_as_a_returns_contract() -> None:
    """A raises() contract written with the wrong verb is caught at the call."""
    with pytest.raises(TypeError) as refusal:
        returns(ValueError, _classify, -1)

    assert "tlaw.raises" in str(refusal.value)


def test_and_then_states_a_second_claim_about_the_returned_value() -> None:
    matched = returns(Err, _classify, -1).and_then(
        lambda failure: tlaw.same(failure.error, "negative")
    )
    mismatched = returns(Err, _classify, -1).and_then(
        lambda failure: tlaw.same(failure.error, "positive")
    )

    assert bool(matched) is True
    assert bool(mismatched) is False
    assert "first difference" in mismatched.explain()


def test_and_then_keeps_the_outcome_when_the_wrong_branch_came_back() -> None:
    """The first claim failing must not be reported as the second one failing."""
    outcome = returns(Err, _classify, 3).and_then(
        lambda failure: tlaw.same(failure.error, "unreachable")
    )

    assert isinstance(outcome, tlaw.Outcome)
    assert "returned Ok(value=6)" in outcome.explain()


def test_a_law_returning_an_outcome_passes_and_fails_with_the_outcome() -> None:
    @tlaw.law
    def negatives_take_the_failure_branch(value: int):
        return returns(Err, _classify, -abs(value) - 1)

    negatives_take_the_failure_branch()

    @tlaw.law
    def everything_takes_the_failure_branch(value: int):
        return returns(Err, _classify, abs(value))

    with pytest.raises(AssertionError) as failure:
        everything_takes_the_failure_branch()

    assert "expected Err, but the call returned Ok" in str(failure.value)


def test_awaiting_inside_a_running_loop_is_refused_not_guessed() -> None:
    async def classify(value: int) -> Ok | Err:
        return _classify(value)

    async def from_inside_a_loop() -> None:
        returns(Err, classify, -1)

    with pytest.raises(RuntimeError) as refusal:
        asyncio.run(from_inside_a_loop())

    assert "already running" in str(refusal.value)
