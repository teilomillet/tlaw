from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tlaw.category import Arrow, arrow, assert_equivalent, identity


def affine(multiplier: int, offset: int, name: str) -> Arrow[int, int]:
    return arrow(lambda value: multiplier * value + offset, name=name)


@given(
    value=st.integers(min_value=-100, max_value=100),
    coefficients=st.tuples(
        st.integers(min_value=-5, max_value=5),
        st.integers(min_value=-20, max_value=20),
        st.integers(min_value=-5, max_value=5),
        st.integers(min_value=-20, max_value=20),
        st.integers(min_value=-5, max_value=5),
        st.integers(min_value=-20, max_value=20),
    ),
)
def test_arrow_composition_is_associative(
    value: int, coefficients: tuple[int, int, int, int, int, int]
) -> None:
    a, b, c, d, e, f = coefficients
    first = affine(a, b, "first")
    second = affine(c, d, "second")
    third = affine(e, f, "third")

    left_grouping = (first >> second) >> third
    right_grouping = first >> (second >> third)

    assert left_grouping == right_grouping
    assert left_grouping(value) == right_grouping(value)


@given(
    value=st.integers(min_value=-10_000, max_value=10_000),
    multiplier=st.integers(min_value=-20, max_value=20),
    offset=st.integers(min_value=-100, max_value=100),
)
def test_identity_does_nothing_on_either_side(
    value: int, multiplier: int, offset: int
) -> None:
    operation = affine(multiplier, offset, "operation")

    assert identity() >> operation == operation
    assert operation >> identity() == operation
    assert (identity() >> operation)(value) == operation(value)
    assert (operation >> identity())(value) == operation(value)


def test_equivalence_failure_names_both_paths_and_observations() -> None:
    left = affine(1, 1, "add one")
    right = identity("leave alone")

    with pytest.raises(AssertionError) as caught:
        assert_equivalent(left, right, 3)

    message = str(caught.value)
    assert "add one" in message
    assert "leave alone" in message
    assert "4" in message
    assert "3" in message
