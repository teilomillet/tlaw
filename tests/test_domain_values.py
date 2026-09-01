"""Low-level laws for visible domains before authoring integration."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

import tlaw


Trees = tlaw.recursive(
    tlaw.integer(0, 9),
    lambda child: tlaw.list_of(child, max_size=3),
    min_leaves=1,
    max_leaves=12,
)


@dataclass(frozen=True)
class RecursiveNode:
    child: object


ConstructedTrees = tlaw.recursive(
    tlaw.integer(0, 9),
    lambda child: tlaw.record(RecursiveNode, child=child),
    max_leaves=4,
)


def _tree_leaves(value: int | list | dict[str, object]) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return _tree_leaves(value["child"])
    return [leaf for child in value for leaf in _tree_leaves(child)]


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
    assert repr(Trees) == (
        "recursive(integer(0, 9), extend=list_of(self, max_size=3), "
        "min_leaves=1, max_leaves=12)"
    )


@given(data=st.data())
def test_recursive_domains_generate_only_the_visible_finite_shape(
    data: st.DataObject,
) -> None:
    tree = data.draw(Trees.strategy)
    leaves = _tree_leaves(tree)

    assert 1 <= len(leaves) <= 12
    assert all(0 <= leaf <= 9 for leaf in leaves)


def test_recursive_domains_generate_through_bare_law_annotations() -> None:
    @tlaw.law
    def every_tree_keeps_its_declared_leaves(tree: Trees) -> bool:
        leaves = _tree_leaves(tree)
        return 1 <= len(leaves) <= 12 and all(0 <= leaf <= 9 for leaf in leaves)

    every_tree_keeps_its_declared_leaves()


def test_recursive_accepts_a_plain_type_as_its_leaf_domain() -> None:
    domain = tlaw.recursive(
        bool,
        lambda child: tlaw.list_of(child, max_size=1),
        max_leaves=2,
    )

    assert repr(domain) == (
        "recursive(bool, extend=list_of(self, max_size=1), max_leaves=2)"
    )
    assert domain.generates == (bool, list)


@given(data=st.data())
def test_recursive_composes_through_record_domains(data: st.DataObject) -> None:
    domain = tlaw.recursive(
        tlaw.integer(0, 9),
        lambda child: tlaw.record(child=child),
        max_leaves=4,
    )

    value = data.draw(domain.strategy)

    assert 0 <= _tree_leaves(value)[0] <= 9
    assert domain.generates == (int, dict)
    assert "extend=record(child=self)" in repr(domain)


@given(data=st.data())
def test_recursive_composes_through_constructed_record_domains(
    data: st.DataObject,
) -> None:
    value = data.draw(ConstructedTrees.strategy)

    while isinstance(value, RecursiveNode):
        value = value.child

    assert isinstance(value, int) and 0 <= value <= 9
    assert ConstructedTrees.generates == (int, RecursiveNode)
    assert "extend=record(RecursiveNode, child=self)" in repr(ConstructedTrees)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_leaves": 0}, "max_leaves"),
        ({"max_leaves": True}, "max_leaves"),
        ({"min_leaves": 0}, "min_leaves"),
        ({"min_leaves": True}, "min_leaves"),
        ({"min_leaves": 3, "max_leaves": 2}, "cannot exceed"),
    ],
)
def test_recursive_refuses_impossible_or_ambiguous_leaf_budgets(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        tlaw.recursive(
            int,
            lambda child: tlaw.list_of(child, max_size=2),
            **kwargs,
        )


def test_recursive_refuses_builders_that_do_not_state_real_recursion() -> None:
    with pytest.raises(TypeError, match="base"):
        tlaw.recursive("not a domain", lambda child: tlaw.list_of(child))
    with pytest.raises(TypeError, match="extend"):
        tlaw.recursive(int, "not a builder")
    with pytest.raises(TypeError, match="exactly one"):
        tlaw.recursive(int, lambda child, other: tlaw.list_of(child))
    with pytest.raises(TypeError, match="exactly one"):
        tlaw.recursive(int, lambda: tlaw.list_of(int))
    with pytest.raises(TypeError, match="exactly one"):
        tlaw.recursive(int, lambda child, other=None: tlaw.list_of(child))
    with pytest.raises(TypeError, match="exactly one"):
        tlaw.recursive(int, lambda *children: tlaw.list_of(children[0]))
    with pytest.raises(TypeError, match="exactly one"):
        tlaw.recursive(int, lambda *, child: tlaw.list_of(child))
    with pytest.raises(TypeError, match="return a tlaw domain"):
        tlaw.recursive(int, lambda child: "not a domain")
    with pytest.raises(ValueError, match="visible shape"):
        tlaw.recursive(int, lambda child: tlaw.list_of(int))
    with pytest.raises(ValueError, match="description text alone"):
        tlaw.recursive(int, lambda child: tlaw.one_of([child]))
    with pytest.raises(ValueError, match="description text alone"):
        tlaw.recursive(
            int,
            lambda child: tlaw.Domain(
                f"opaque({child})",
                child.strategy,
            ),
        )


def test_recursive_refuses_a_builder_whose_visible_shape_changes() -> None:
    calls = 0

    def unstable(child: tlaw.Domain) -> tlaw.Domain:
        nonlocal calls
        calls += 1
        return tlaw.list_of(child, max_size=calls)

    with pytest.raises(ValueError, match="stable visible shape"):
        tlaw.recursive(int, unstable)


def test_impossible_domains_are_refused_when_defined() -> None:
    with pytest.raises(ValueError):
        tlaw.number(1, 0)
    with pytest.raises(TypeError):
        tlaw.integer(True, 2)
    with pytest.raises(ValueError):
        tlaw.one_of(())
    with pytest.raises(ValueError):
        tlaw.list_of(int, min_size=2, max_size=1)


@pytest.mark.parametrize("bound", [2**53 + 1, -(2**53 + 1), 10**400])
def test_number_refuses_integer_bounds_it_cannot_represent_exactly(bound: int) -> None:
    """The visible integer bound must not be rounded into a wider domain."""
    with pytest.raises(ValueError, match="exactly representable as a finite float"):
        tlaw.number(bound, bound)


@given(data=st.data())
def test_number_accepts_the_largest_consecutive_exact_integer_bound(
    data: st.DataObject,
) -> None:
    bound = 2**53

    domain = tlaw.number(bound, bound)

    assert data.draw(domain.strategy) == float(bound)


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
