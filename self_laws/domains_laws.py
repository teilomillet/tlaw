"""Self-laws for tlaw's visible input-domain boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product

import tlaw
from hypothesis import HealthCheck, Phase, given, seed, settings
from hypothesis import strategies as st
from tlaw.domains import integer, list_of, number, one_of, recursive
from tlaw.finite import finite_plan


_draw_seeds = st.integers(0, 2**32 - 1)
_sampled = settings(
    max_examples=100,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.nested_given],
)


def _samples(domain: tlaw.Domain, draw_seed: int) -> list[object]:
    """Observe a fixed schedule whose ordinary seed can be retained and replayed."""
    values: list[object] = []

    @seed(draw_seed)
    @settings(
        max_examples=2,
        phases=(Phase.generate,),
        database=None,
        deadline=None,
    )
    @given(domain.strategy)
    def collect(value: object) -> None:
        values.append(value)

    collect()
    return values


def _leaves(value: int | list | dict[str, object]) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return _leaves(value["child"])
    return [leaf for child in value for leaf in _leaves(child)]


@_sampled
@tlaw.law(
    "number domains generate finite floats inside their closed bounds",
    low=st.integers(-1000, 1000),
    width=st.integers(0, 100),
    draw_seed=_draw_seeds,
)
def number_domains_generate_finite_values(low: int, width: int, draw_seed: int) -> bool:
    high = low + width
    domain = number(low, high)
    return all(
        isinstance(value, float)
        and math.isfinite(value)
        and low <= value <= high
        and repr(domain) == f"number({low}, {high})"
        for value in _samples(domain, draw_seed)
    )


@_sampled
@tlaw.law(
    "integer domains generate integers inside their closed bounds",
    low=st.integers(-1000, 1000),
    width=st.integers(0, 100),
    draw_seed=_draw_seeds,
)
def integer_domains_generate_closed_values(
    low: int, width: int, draw_seed: int
) -> bool:
    high = low + width
    domain = integer(low, high)
    return all(
        type(value) is int
        and low <= value <= high
        and repr(domain) == f"integer({low}, {high})"
        for value in _samples(domain, draw_seed)
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


@_sampled
@tlaw.law(
    "finite choices generate only a stated member",
    values=st.lists(st.integers(-20, 20), min_size=1, max_size=8, unique=True),
    draw_seed=_draw_seeds,
)
def finite_choices_generate_only_members(values: list[int], draw_seed: int) -> bool:
    domain = one_of(values)
    return all(value in values for value in _samples(domain, draw_seed))


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


@_sampled
@tlaw.law(
    "list domains enforce both visible size bounds",
    minimum=st.integers(0, 4),
    extra=st.integers(0, 4),
    draw_seed=_draw_seeds,
)
def list_domains_enforce_visible_sizes(
    minimum: int, extra: int, draw_seed: int
) -> bool:
    maximum = minimum + extra
    domain = list_of(integer(0, 9), min_size=minimum, max_size=maximum)
    return all(
        minimum <= len(values) <= maximum
        and all(0 <= value <= 9 for value in values)
        and f"max_size={maximum}" in repr(domain)
        for values in _samples(domain, draw_seed)
    )


@_sampled
@tlaw.law(
    "plain-type unbounded lists keep their element and minimum visible",
    draw_seed=_draw_seeds,
)
def plain_type_lists_keep_their_open_upper_bound_visible(draw_seed: int) -> bool:
    domain = list_of(int, min_size=1)
    return all(
        len(values) >= 1
        and all(type(value) is int for value in values)
        and repr(domain) == "list_of(int, min_size=1)"
        for values in _samples(domain, draw_seed)
    )


@_sampled
@tlaw.law(
    "recursive domains expose and enforce their finite leaf budget",
    draw_seed=_draw_seeds,
)
def recursive_domains_enforce_their_leaf_budget(draw_seed: int) -> bool:
    domain = recursive(
        integer(0, 9),
        lambda child: list_of(child, max_size=3),
        min_leaves=1,
        max_leaves=12,
    )
    return all(
        1 <= len(leaves) <= 12
        and all(0 <= leaf <= 9 for leaf in leaves)
        and repr(domain)
        == (
            "recursive(integer(0, 9), extend=list_of(self, max_size=3), "
            "min_leaves=1, max_leaves=12)"
        )
        for leaves in (_leaves(tree) for tree in _samples(domain, draw_seed))
    )


@_sampled
@tlaw.law(
    "recursive records compose the child strategy as well as its description",
    draw_seed=_draw_seeds,
)
def recursive_record_domains_retain_child_provenance(draw_seed: int) -> bool:
    domain = recursive(
        integer(0, 9),
        lambda child: tlaw.record(child=child),
        max_leaves=4,
    )
    return all(
        0 <= _leaves(value)[0] <= 9
        and domain.generates == (int, dict)
        and "extend=record(child=self)" in repr(domain)
        for value in _samples(domain, draw_seed)
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


@_sampled
@tlaw.law(
    "equal recursive leaf budgets describe a valid exact boundary",
    draw_seed=_draw_seeds,
)
def equal_recursive_leaf_budgets_are_valid(draw_seed: int) -> bool:
    domain = recursive(
        int,
        lambda child: list_of(child, min_size=1, max_size=1),
        min_leaves=1,
        max_leaves=1,
    )
    return all(
        len(_leaves(value)) == 1 and "min_leaves=1, max_leaves=1" in repr(domain)
        for value in _samples(domain, draw_seed)
    )


@_sampled
@tlaw.law(
    "dependent domains resolve only from their named inputs",
    high=st.integers(1, 20),
    draw_seed=_draw_seeds,
)
def dependent_domains_resolve_named_inputs(high: int, draw_seed: int) -> bool:
    dependent = tlaw.depends(lambda high: integer(1, high))
    resolved = dependent.resolve({"high": high})
    return all(
        dependent.dependencies == ("high",) and 1 <= value <= high
        for value in _samples(resolved, draw_seed)
    )


@_sampled
@tlaw.law(
    "record dependencies hold by construction",
    draw_seed=_draw_seeds,
)
def record_dependencies_hold_by_construction(draw_seed: int) -> bool:
    domain = tlaw.record(
        high=integer(1, 20),
        value=tlaw.depends(lambda high: integer(1, high)),
    )
    return all(
        1 <= record["value"] <= record["high"] for record in _samples(domain, draw_seed)
    )


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


@_sampled
@tlaw.law(
    "finite integer declarations include both endpoints",
    low=st.integers(-3, 3),
    width=st.integers(0, 4),
)
def finite_integer_declarations_include_both_endpoints(low: int, width: int) -> bool:
    high = low + width
    plan = finite_plan({"value": integer(low, high)}, limit=5)
    return (
        list(plan.arguments()) == [{"value": value} for value in range(low, high + 1)]
        and plan.total == width + 1
        and plan.list_bounds == {}
    )


@_sampled
@tlaw.law(
    "finite choices preserve order, duplicates, and concrete types",
    values=st.lists(
        st.one_of(st.booleans(), st.integers(-2, 2)), min_size=1, max_size=4
    ),
)
def finite_choices_preserve_order_duplicates_and_concrete_types(values: list) -> bool:
    plan = finite_plan({"value": one_of(values)}, limit=4)
    observed = [case["value"] for case in plan.arguments()]
    return (
        observed == values
        and [type(value) for value in observed] == [type(value) for value in values]
        and plan.total == len(values)
    )


@_sampled
@tlaw.law(
    "finite lists enumerate each bounded history",
    minimum=st.integers(0, 2),
    extra=st.integers(0, 2),
)
def finite_lists_enumerate_each_bounded_history(minimum: int, extra: int) -> bool:
    maximum = minimum + extra
    expected = [
        {"history": list(history)}
        for size in range(minimum, maximum + 1)
        for history in product([-1, 0, 1], repeat=size)
    ]
    domain = list_of(integer(-1, 1), min_size=minimum, max_size=maximum)
    plan = finite_plan({"history": domain}, limit=121)
    return (
        list(plan.arguments()) == expected
        and plan.total == len(expected)
        and plan.list_bounds == {"history": {"min_size": minimum, "max_size": maximum}}
    )


@_sampled
@tlaw.law(
    "finite records enumerate the independent field product",
    minimum=st.integers(0, 1),
    extra=st.integers(0, 1),
)
def finite_records_enumerate_the_independent_field_product(
    minimum: int, extra: int
) -> bool:
    maximum = minimum + extra
    histories = [
        list(history)
        for size in range(minimum, maximum + 1)
        for history in product([0, 1], repeat=size)
    ]
    expected = [
        {"request": {"active": active, "history": history}}
        for active in [False, True]
        for history in histories
    ]
    domain = tlaw.record(
        active=bool,
        history=list_of(one_of([0, 1]), min_size=minimum, max_size=maximum),
    )
    plan = finite_plan({"request": domain}, limit=14)
    return (
        list(plan.arguments()) == expected
        and plan.total == len(expected)
        and plan.list_bounds
        == {"request.history": {"min_size": minimum, "max_size": maximum}}
    )
