from __future__ import annotations

from typing import Annotated

from hypothesis import strategies as st
import pytest

import tlaw


class Counter:
    def __init__(self, value: int) -> None:
        self.value = value

    def add(self, amount: int) -> None:
        self.value += amount


def test_mutating_path_is_compared_from_two_fresh_starts() -> None:
    counter = tlaw.system(Counter, observe=lambda value: value.value)
    start = counter(1)

    equation = start.call("add", 2).same_as(counter(3))

    assert equation.check("adding changes the value") is None
    assert start.result() == 1


def test_pure_round_trip_reads_as_a_path_equation() -> None:
    text = tlaw.system(lambda value: value)
    start = text("hello")

    equation = start.then(str.encode).then(bytes.decode).same_as(start)

    assert equation.check("UTF-8 round trip") is None


def test_failed_equation_explains_both_paths_and_observations() -> None:
    counter = tlaw.system(Counter, observe=lambda value: value.value, name="counter")

    with pytest.raises(AssertionError) as caught:
        counter(1).call("add", 1).same_as(counter(1)).check("adding is identity")

    message = str(caught.value)
    assert "adding is identity" in message
    assert "counter(1).add(1)" in message
    assert "counter(1)" in message
    assert "2" in message
    assert "1" in message


def test_law_decorator_runs_hypothesis_without_requiring_a_test_name() -> None:
    values = tlaw.system(lambda value: value)

    @tlaw.law("double negation is identity", value=st.integers())
    def double_negation(value: int):
        start = values(value)
        return (
            start.then(lambda number: -number, name="negate")
            .then(lambda number: -number, name="negate")
            .same_as(start)
        )

    assert double_negation.__test__ is True
    double_negation()


def test_bare_law_infers_inputs_and_accepts_a_boolean_equation() -> None:
    @tlaw.law
    def reversing_twice_returns_the_original(values: list[int]):
        return list(reversed(list(reversed(values)))) == values

    assert reversing_twice_returns_the_original.__test__ is True
    assert (
        reversing_twice_returns_the_original.__tlaw_description__
        == "reversing twice returns the original"
    )
    assert reversing_twice_returns_the_original.__tlaw_inputs__ == (
        "values: list[int]",
    )
    assert reversing_twice_returns_the_original.__tlaw_max_examples__ == 100
    assert reversing_twice_returns_the_original.__tlaw_deterministic__ is True
    reversing_twice_returns_the_original()


def test_failed_boolean_law_uses_the_function_name() -> None:
    @tlaw.law
    def adding_one_changes_nothing(value: int):
        return value + 1 == value

    with pytest.raises(AssertionError, match="law failed: adding one changes nothing"):
        adding_one_changes_nothing()


def test_bare_law_rejects_a_false_pass_after_mutating_its_input() -> None:
    @tlaw.law
    def clearing_a_list_changes_nothing(values: list[int]):
        values.clear()
        return values == values

    with pytest.raises(
        AssertionError,
        match="passed after mutating generated input 'values'",
    ):
        clearing_a_list_changes_nothing()


def test_ambiguous_result_names_the_returned_type() -> None:
    @tlaw.law
    def returning_many_answers_is_not_one_law(value: int):
        return [value == value]

    with pytest.raises(
        TypeError,
        match=r"returned builtins\.list, not one Python boolean",
    ):
        returning_many_answers_is_not_one_law()


def test_bare_law_refuses_to_ignore_annotated_domain_metadata() -> None:
    with pytest.raises(
        TypeError,
        match="does not yet enforce Annotated metadata for: value",
    ):

        @tlaw.law
        def positive_values_are_generated(value: Annotated[int, "positive"]):
            return value > 0
