"""Self-laws for tlaw's visible input-domain boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass

import tlaw
from hypothesis import settings
from hypothesis import strategies as st
from tlaw.domains import integer, list_of, number, one_of, recursive


def _leaves(value: int | list | dict[str, object]) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return _leaves(value["child"])
    return [leaf for child in value for leaf in _leaves(child)]


@tlaw.law(
    "number domains generate finite floats inside their closed bounds",
    low=st.integers(-1000, 1000),
    width=st.integers(0, 100),
    data=st.data(),
)
def number_domains_generate_finite_values(low: int, width: int, data) -> bool:
    high = low + width
    domain = number(low, high)
    value = data.draw(domain.strategy)
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and low <= value <= high
        and repr(domain) == f"number({low}, {high})"
    )


@tlaw.law(
    "integer domains generate integers inside their closed bounds",
    low=st.integers(-1000, 1000),
    width=st.integers(0, 100),
    data=st.data(),
)
def integer_domains_generate_closed_values(low: int, width: int, data) -> bool:
    high = low + width
    domain = integer(low, high)
    value = data.draw(domain.strategy)
    return (
        type(value) is int
        and low <= value <= high
        and repr(domain) == f"integer({low}, {high})"
    )


@tlaw.law(
    "numeric domains reject bounds that would make their descriptions lie",
    invalid=st.sampled_from([True, math.nan, math.inf, -math.inf, 2**53 + 1, 10**400]),
)
def numeric_domains_reject_dishonest_bounds(invalid) -> bool:
    try:
        number(invalid, invalid)
    except (TypeError, ValueError):
        return True
    return False


@tlaw.law(
    "finite choices generate only a stated member",
    values=st.lists(st.integers(-20, 20), min_size=1, max_size=8, unique=True),
    data=st.data(),
)
def finite_choices_generate_only_members(values: list[int], data) -> bool:
    domain = one_of(values)
    return data.draw(domain.strategy) in values


@tlaw.law(
    "finite choice descriptions summarize only above six members",
    value=st.integers(),
)
def finite_choice_descriptions_have_an_exact_readability_boundary(value: int) -> bool:
    six = one_of(range(6))
    seven = one_of(range(7))
    return (
        repr(six) == "one_of(0, 1, 2, 3, 4, 5)"
        and repr(seven) == "one_of(0, 1, 2, 3, ... (7 values))"
    )


@tlaw.law(
    "list domains enforce both visible size bounds",
    minimum=st.integers(0, 4),
    extra=st.integers(0, 4),
    data=st.data(),
)
def list_domains_enforce_visible_sizes(minimum: int, extra: int, data) -> bool:
    maximum = minimum + extra
    domain = list_of(integer(0, 9), min_size=minimum, max_size=maximum)
    values = data.draw(domain.strategy)
    return (
        minimum <= len(values) <= maximum
        and all(0 <= value <= 9 for value in values)
        and f"max_size={maximum}" in repr(domain)
    )


@tlaw.law(
    "plain-type unbounded lists keep their element and minimum visible",
    data=st.data(),
)
def plain_type_lists_keep_their_open_upper_bound_visible(data) -> bool:
    domain = list_of(int, min_size=1)
    values = data.draw(domain.strategy)
    return (
        len(values) >= 1
        and all(type(value) is int for value in values)
        and repr(domain) == "list_of(int, min_size=1)"
    )


@tlaw.law(
    "recursive domains expose and enforce their finite leaf budget",
    data=st.data(),
)
def recursive_domains_enforce_their_leaf_budget(data) -> bool:
    domain = recursive(
        integer(0, 9),
        lambda child: list_of(child, max_size=3),
        min_leaves=1,
        max_leaves=12,
    )
    tree = data.draw(domain.strategy)
    leaves = _leaves(tree)
    return (
        1 <= len(leaves) <= 12
        and all(0 <= leaf <= 9 for leaf in leaves)
        and repr(domain)
        == (
            "recursive(integer(0, 9), extend=list_of(self, max_size=3), "
            "min_leaves=1, max_leaves=12)"
        )
    )


@tlaw.law(
    "recursive records compose the child strategy as well as its description",
    data=st.data(),
)
def recursive_record_domains_retain_child_provenance(data) -> bool:
    domain = recursive(
        integer(0, 9),
        lambda child: tlaw.record(child=child),
        max_leaves=4,
    )
    value = data.draw(domain.strategy)
    return (
        0 <= _leaves(value)[0] <= 9
        and domain.generates == (int, dict)
        and "extend=record(child=self)" in repr(domain)
    )


@tlaw.law(
    "recursive domains reject vacuous builders and impossible budgets",
    value=st.integers(),
)
def recursive_domains_reject_vacuous_or_impossible_shapes(value: int) -> bool:
    cases = [
        lambda: recursive(int, lambda child: list_of(int)),
        lambda: recursive(int, lambda child: list_of(child), max_leaves=0),
        lambda: recursive(int, lambda child: list_of(child), min_leaves=0),
        lambda: recursive(
            int,
            lambda child: list_of(child),
            min_leaves=2,
            max_leaves=1,
        ),
        lambda: recursive(
            int,
            lambda child, other: list_of(child),
        ),
        lambda: recursive(int, lambda: list_of(int)),
        lambda: recursive(int, lambda child, other=None: list_of(child)),
        lambda: recursive(int, lambda *children: list_of(children[0])),
        lambda: recursive(int, lambda *, child: list_of(child)),
        lambda: recursive(int, lambda child: one_of([child])),
        lambda: recursive(
            int,
            lambda child: tlaw.Domain(f"opaque({child})", child.strategy),
        ),
        lambda: recursive(value, lambda child: list_of(child)),
    ]
    for build in cases:
        try:
            build()
        except (TypeError, ValueError):
            continue
        return False
    return True


@tlaw.law(
    "equal recursive leaf budgets describe a valid exact boundary",
    data=st.data(),
)
def equal_recursive_leaf_budgets_are_valid(data) -> bool:
    domain = recursive(
        int,
        lambda child: list_of(child, min_size=1, max_size=1),
        min_leaves=1,
        max_leaves=1,
    )
    value = data.draw(domain.strategy)
    return len(_leaves(value)) == 1 and "min_leaves=1, max_leaves=1" in repr(domain)


@tlaw.law(
    "dependent domains resolve only from their named inputs",
    high=st.integers(1, 20),
    data=st.data(),
)
def dependent_domains_resolve_named_inputs(high: int, data) -> bool:
    dependent = tlaw.depends(lambda high: integer(1, high))
    resolved = dependent.resolve({"high": high})
    value = data.draw(resolved.strategy)
    return dependent.dependencies == ("high",) and 1 <= value <= high


@tlaw.law(
    "record dependencies hold by construction",
    data=st.data(),
)
def record_dependencies_hold_by_construction(data) -> bool:
    domain = tlaw.record(
        high=integer(1, 20),
        value=tlaw.depends(lambda high: integer(1, high)),
    )
    record = data.draw(domain.strategy)
    return 1 <= record["value"] <= record["high"]


@settings(deadline=None)
@tlaw.law(
    "record descriptions expose domain, dependency, type, and constructor fields",
    value=st.integers(),
)
def record_descriptions_expose_every_field_shape(value: int) -> bool:
    @dataclass
    class Box:
        value: int

    dependent = tlaw.record(
        high=integer(1, 3),
        value=tlaw.depends(lambda high: integer(1, high)),
    )
    typed = tlaw.record(name=str)
    constructed = tlaw.record(Box, value=integer(0, 1))
    return (
        repr(dependent) == "record(high=integer(1, 3), value=depends(high))"
        and repr(typed) == "record(name=str)"
        and repr(constructed) == "record(Box, value=integer(0, 1))"
        and constructed.generates == (Box,)
    )
