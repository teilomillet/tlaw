"""Low-level laws for visible domains before authoring integration."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

import tlaw


@given(data=st.data())
def test_number_and_integer_stay_inside_their_bounds(data: st.DataObject) -> None:
    number = data.draw(tlaw.number(-1, 1).strategy)
    integer = data.draw(tlaw.integer(2, 5).strategy)

    assert math.isfinite(number) and -1 <= number <= 1
    assert isinstance(integer, int) and 2 <= integer <= 5


@given(data=st.data())
def test_lists_and_finite_choices_keep_their_declared_shape(
    data: st.DataObject,
) -> None:
    choice = tlaw.one_of((2, 4, 8))
    values = data.draw(tlaw.list_of(choice, min_size=1, max_size=4).strategy)

    assert 1 <= len(values) <= 4
    assert set(values) <= {2, 4, 8}


def test_domain_descriptions_are_visible() -> None:
    assert repr(tlaw.number(0, 1)) == "number(0, 1)"
    assert repr(tlaw.integer(1, 8)) == "integer(1, 8)"
    assert repr(tlaw.one_of((1, 2))) == "one_of(1, 2)"
    assert repr(tlaw.list_of(int)) == "list_of(int)"


def test_impossible_domains_are_refused_when_defined() -> None:
    with pytest.raises(ValueError):
        tlaw.number(1, 0)
    with pytest.raises(TypeError):
        tlaw.integer(True, 2)
    with pytest.raises(ValueError):
        tlaw.one_of(())
    with pytest.raises(ValueError):
        tlaw.list_of(int, min_size=2, max_size=1)


def test_dependencies_are_named_and_resolved_explicitly() -> None:
    dependent = tlaw.depends(lambda high: tlaw.integer(1, high))

    assert dependent.dependencies == ("high",)
    assert repr(dependent) == "depends(high)"
    assert repr(dependent.resolve({"high": 3})) == "integer(1, 3)"


def test_positional_only_dependency_builders_are_refused_early() -> None:
    def positional_only(high: int, /) -> tlaw.Domain:
        return tlaw.integer(1, high)

    with pytest.raises(TypeError, match="positional-only"):
        tlaw.depends(positional_only)
