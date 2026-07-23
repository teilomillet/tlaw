"""Laws for `tlaw.raises`: the one spelling of an exception contract.

Born from the law-first experiment (docs/law-first-experiment.md): the
try/except spelling reported only False on failure, hiding whether the
wrong exception, no exception, or the wrong outcome occurred.
"""

from __future__ import annotations

import asyncio

import pytest

import tlaw
from tlaw import raises


def _refuse(value: int) -> int:
    if value < 0:
        raise ValueError("negative")
    return value * 2


def test_the_expected_exception_is_a_truthy_refusal() -> None:
    refusal = raises(ValueError, _refuse, -1)

    assert refusal.matched
    assert bool(refusal) is True
    assert "as required" in refusal.explain()


def test_a_subclass_counts_as_refused_like_ordinary_except() -> None:
    def indexerror() -> None:
        raise IndexError("out of range")

    assert raises(LookupError, indexerror).matched


def test_a_returned_value_is_named_not_reduced_to_false() -> None:
    refusal = raises(ValueError, _refuse, 3)

    assert not refusal.matched
    assert "returned 6 instead of raising" in refusal.explain()


def test_the_wrong_exception_is_named_not_reduced_to_false() -> None:
    def typeerror() -> None:
        raise TypeError("wrong shape")

    refusal = raises(ValueError, typeerror)

    assert not refusal.matched
    assert "raised TypeError: wrong shape" in refusal.explain()


def test_a_law_returning_a_refusal_passes_and_fails_with_the_outcome() -> None:
    @tlaw.law
    def negatives_are_refused(value: int):
        return raises(ValueError, _refuse, -abs(value) - 1)

    negatives_are_refused()

    @tlaw.law
    def everything_is_refused(value: int):
        return raises(ValueError, _refuse, abs(value))

    with pytest.raises(AssertionError) as failure:
        everything_is_refused()

    assert "instead of raising" in str(failure.value)


def test_an_unnamed_non_exception_exit_propagates_instead_of_becoming_evidence() -> (
    None
):
    """A law about ValueError must never swallow the user's interrupt."""

    def interrupt() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        raises(ValueError, interrupt)


def test_a_named_base_exception_is_observed_because_the_law_asked_for_it() -> None:
    """anef's cancellation contract (issue #1, gap G5) lives outside Exception."""

    def cancel() -> None:
        raise asyncio.CancelledError

    assert raises(asyncio.CancelledError, cancel).matched


def test_the_expected_type_must_be_an_exception_class() -> None:
    with pytest.raises(TypeError):
        raises("ValueError", _refuse, -1)
    with pytest.raises(TypeError):
        raises(int, _refuse, -1)


def test_an_async_target_is_awaited_so_the_real_outcome_is_observed() -> None:
    """Calling an async def only builds a coroutine; the work has not happened."""

    async def cancel(value: int) -> int:
        raise asyncio.CancelledError

    refusal = raises(asyncio.CancelledError, cancel, 1)

    assert refusal.matched
    assert refusal.awaited is True
    assert "the awaited call raised CancelledError as required" == refusal.explain()


def test_an_async_target_that_returns_names_the_value_not_the_coroutine() -> None:
    async def double(value: int) -> int:
        return value * 2

    refusal = raises(asyncio.CancelledError, double, 3)

    assert not refusal.matched
    assert "the awaited call returned 6 instead of raising" in refusal.explain()


def test_a_sync_target_is_not_reported_as_awaited() -> None:
    refusal = raises(ValueError, _refuse, -1)

    assert refusal.awaited is False
    assert "the call raised ValueError as required" == refusal.explain()


def test_and_then_states_a_second_claim_about_the_raised_exception() -> None:
    def explode() -> None:
        raise RuntimeError("upstream timed out")

    matched = raises(RuntimeError, explode).and_then(
        lambda error: tlaw.same(str(error), "upstream timed out")
    )
    mismatched = raises(RuntimeError, explode).and_then(
        lambda error: tlaw.same(str(error), "something else")
    )

    assert bool(matched) is True
    assert bool(mismatched) is False
    assert "first difference" in mismatched.explain()


def test_and_then_keeps_the_refusal_when_the_call_did_not_raise() -> None:
    """The first claim failing must not be reported as the second one failing."""
    outcome = raises(ValueError, _refuse, 3).and_then(
        lambda error: tlaw.same(str(error), "unreachable")
    )

    assert isinstance(outcome, tlaw.Refusal)
    assert "returned 6 instead of raising" in outcome.explain()
