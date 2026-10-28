from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


@dataclass
class _MutableSample:
    score: float
    items: list[int]
    peer: object = None

    def __deepcopy__(self, memo: dict[int, object]) -> _MutableSample:
        if id(self) in memo:
            return memo[id(self)]  # type: ignore[return-value]
        copied = _MutableSample(
            score=float("nan") if self.score != self.score else float(self.score),
            items=list(self.items),
        )
        memo[id(self)] = copied
        copied.peer = copied if self.peer is self else self.peer
        return copied


SampleDomain = tlaw.record(
    _MutableSample,
    score=tlaw.one_of([float("nan"), 1.0]),
    items=tlaw.list_of(tlaw.integer(0, 2), max_size=2),
)


def test_input_mutation_guard_handles_dataclass_nan_cycles_and_mutations() -> None:
    from tlaw.authoring import _copied_value_is_unchanged

    cyclic = _MutableSample(score=float("nan"), items=[1, 2])
    cyclic.peer = cyclic
    copied = cyclic.__deepcopy__({})
    assert _copied_value_is_unchanged(cyclic, copied) is True

    copied.items.append(3)
    assert _copied_value_is_unchanged(cyclic, copied) is False

    @tlaw.law
    def pure_dataclass_law(sample: SampleDomain) -> bool:
        return isinstance(sample.items, list)

    pure_dataclass_law()

    @tlaw.law
    def mutating_dataclass_law(sample: SampleDomain) -> bool:
        sample.items.append(99)
        return True

    with pytest.raises(
        AssertionError,
        match="passed after mutating generated input 'sample'",
    ):
        mutating_dataclass_law()


def test_an_unverifiable_input_is_a_harness_limit_not_a_refutation() -> None:
    """A check tlaw cannot perform is no evidence about the code under law.

    The dedicated type lets the execution layer report invalid evidence
    (``tlaw check`` exits ``2``) instead of a refuted law, without parsing
    the message; the message itself still names the fix.
    """
    from tlaw.authoring import UnverifiableInputError

    np = pytest.importorskip("numpy")

    def array_inputs_cannot_be_verified(values):
        return True

    # The annotation is set explicitly because this module imports
    # ``__future__`` annotations and NumPy is imported inside the test, so a
    # string annotation would not resolve against the law function's globals.
    array_inputs_cannot_be_verified.__annotations__ = {"values": np.ndarray}
    law = tlaw.law(array_inputs_cannot_be_verified)

    assert issubclass(UnverifiableInputError, TypeError)
    with pytest.raises(
        UnverifiableInputError,
        match=(
            "could not verify that input 'values' of type ndarray remained unchanged"
        ),
    ):
        law()


def test_a_pure_harness_limit_is_invalid_evidence_not_a_refutation(
    tmp_path: Path,
) -> None:
    """Only a harness limit fired, so the run is invalid, not a refutation."""
    pytest.importorskip("numpy")
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import numpy as np
import tlaw


@tlaw.law
def every_array_is_fine(values: np.ndarray):
    return True
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["summary"]["failures"] == 0
    assert result.report["failures"] == []
    assert result.report["pytest_exit_code"] == 1
    assert "the law above was refuted" not in result.output


def test_a_harness_limit_neither_hides_nor_joins_a_genuine_refutation(
    tmp_path: Path,
) -> None:
    """A genuine refutation stays a refutation; the harness limit is not retained."""
    pytest.importorskip("numpy")
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import numpy as np
import tlaw


@tlaw.law
def numbers_stay_small(value: tlaw.integer(1, 3)):
    return value > 5


@tlaw.law
def every_array_is_fine(values: np.ndarray):
    return True
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 1
    assert result.report["summary"]["status"] == "failed"
    assert [failure["name"] for failure in result.report["failures"]] == [
        "numbers_stay_small"
    ]
    assert all(
        "UnverifiableInputError" not in failure["exception"]["type"]
        for failure in result.report["failures"]
    )
    assert "law failed: numbers stay small" in result.output
    assert "the law above was refuted" not in result.output


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
