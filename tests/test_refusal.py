"""Laws for `tlaw.raises`: the one spelling of an exception contract.

Born from the law-first experiment (docs/law-first-experiment.md): the
try/except spelling reported only False on failure, hiding whether the
wrong exception, no exception, or the wrong outcome occurred.
"""

from __future__ import annotations

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


def test_non_exception_exits_propagate_instead_of_becoming_evidence() -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        raises(ValueError, interrupt)


def test_the_expected_type_must_be_an_exception_class() -> None:
    with pytest.raises(TypeError):
        raises("ValueError", _refuse, -1)
    with pytest.raises(TypeError):
        raises(KeyboardInterrupt, _refuse, -1)
