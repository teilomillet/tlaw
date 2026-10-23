"""Self-laws for tlaw's structural comparison boundary."""

from __future__ import annotations

import math

import tlaw
from tlaw.comparison import same


Leaves = tlaw.one_of([None, False, True, -2, 0, 3, "", "safe"])
Trees = tlaw.recursive(
    Leaves,
    lambda child: tlaw.list_of(child, max_size=3),
    max_leaves=12,
)
SmallInt = tlaw.integer(-100, 100)
NonNegative = tlaw.number(0, 1000)


@tlaw.law
def structural_equality_is_reflexive(value: Trees):
    return same(value, value)


@tlaw.law
def structural_matching_is_symmetric(left: Trees, right: Trees) -> bool:
    return same(left, right).matched is same(right, left).matched


@tlaw.law
def absolute_tolerance_has_its_stated_closed_boundary(
    left: SmallInt,
    right: SmallInt,
    within: NonNegative,
) -> bool:
    expected = left == right or abs(left - right) <= within
    return same(left, right, within=within).matched is expected


@tlaw.law
def relative_tolerance_scales_with_the_larger_magnitude(value: SmallInt) -> bool:
    magnitude = abs(value) + 1
    perturbed = magnitude * 1.000001
    return same(magnitude, perturbed, relative=0.0000011).matched


@tlaw.law
def absolute_tolerance_includes_its_exact_boundary(value: SmallInt) -> bool:
    return same(value, value + 1, within=1).matched


@tlaw.law
def arbitrary_precision_integers_keep_exact_tolerance_math(value: SmallInt) -> bool:
    huge = 10**1000 + value
    return (
        not same(huge, huge + 1).matched and same(huge, huge + 1, relative=1e-9).matched
    )


@tlaw.law
def relative_failures_state_both_tolerance_contracts(value: SmallInt) -> bool:
    comparison = same(value, value + 10, within=1, relative=0.01)
    explanation = comparison.explain()
    return (
        not comparison.matched
        and "within=1.0" in explanation
        and "relative=0.01" in explanation
        and "allowance" in explanation
    )


@tlaw.law
def booleans_never_hide_as_integers(value: SmallInt) -> bool:
    comparison = same(value >= 0, int(value >= 0))
    return not comparison.matched and "bool" in comparison.explain()


@tlaw.law
def nan_never_matches_itself(value: SmallInt) -> bool:
    comparison = same(float("nan"), float("nan"), within=abs(value))
    return not comparison.matched and "NaN" in comparison.explain()


@tlaw.law
def infinities_match_only_when_their_signs_match(value: SmallInt) -> bool:
    sign = math.inf if value >= 0 else -math.inf
    return same(sign, sign).matched and not same(sign, -sign).matched


@tlaw.law
def cycles_use_structural_value_semantics(value: SmallInt) -> bool:
    # Termination is part of this cycle contract; recursion is a stable
    # law refutation even when the stack limit is reached at different sites.
    try:
        left: list[object] = []
        right: list[object] = []
        left.extend([left, {"value": value}])
        right.extend([right, {"value": value}])
        if not same(left, right).matched:
            return False
        right[1] = {"value": value + 1}
        difference = same(left, right).difference
        return (
            difference is not None
            and difference.path == "[1]['value']"
            and difference.left == value
            and difference.right == value + 1
        )
    except RecursionError:
        return False


@tlaw.law
def mapping_cycles_use_the_same_structural_semantics(value: SmallInt) -> bool:
    # Termination is part of this cycle contract; recursion is a stable
    # law refutation even when the stack limit is reached at different sites.
    try:
        left: dict[str, object] = {"value": value}
        right: dict[str, object] = {"value": value}
        left["self"] = left
        right["self"] = right
        if not same(left, right).matched:
            return False
        right["value"] = value + 1
        difference = same(left, right).difference
        return difference is not None and difference.path == "['value']"
    except RecursionError:
        return False


@tlaw.law
def array_like_values_are_normalized_without_a_library_dependency(
    value: SmallInt,
) -> bool:
    class ArrayLike:
        dtype = "int"

        def __init__(self, item: int, shape: tuple[int, ...] = (1,)) -> None:
            self.item = item
            self.shape = shape

        def tolist(self) -> list[int]:
            return [self.item]

    return (
        same(ArrayLike(value), [value]).matched
        and not same(ArrayLike(value, (1,)), ArrayLike(value, (1, 1))).matched
    )


@tlaw.law
def root_and_nested_failures_have_distinct_complete_reports(value: SmallInt) -> bool:
    root = same(value, value + 1).explain().splitlines()
    nested = same({"items": [value]}, {"items": [value + 1]}).explain()
    return (
        root[0] == "first difference at value:"
        and len(root) == 4
        and root[1] == f"  left:  {value}"
        and root[2] == f"  right: {value + 1}"
        and "first difference at value['items'][0]:" in nested
        and f"left:  {{'items': [{value}]}}" in nested
        and f"right: {{'items': [{value + 1}]}}" in nested
    )


@tlaw.law
def reports_truncate_only_above_the_exact_limit(value: SmallInt) -> bool:
    exactly_at_limit = "x" * 198
    above_limit = "x" * 199
    exact = same(exactly_at_limit, str(value)).explain()
    truncated = same(above_limit, str(value)).explain()
    return "... (200 characters)" not in exact and "... (201 characters)" in truncated


@tlaw.law
def sequence_and_mapping_shape_differences_are_located(value: SmallInt) -> bool:
    length = same([value], []).difference
    keys = same({"left": value}, {"right": value}).difference
    return (
        length is not None
        and "lengths differ: 1 vs 0" in length.reason
        and keys is not None
        and "only in left: 'left'" in keys.reason
        and "only in right: 'right'" in keys.reason
    )


@tlaw.law
def chained_claims_run_only_after_a_match(value: SmallInt) -> bool:
    observed = False

    def observe() -> bool:
        nonlocal observed
        observed = True
        return True

    failed = same(value, value + 1)
    if failed.and_then(observe) is not failed or observed:
        return False
    return same(value, value).and_then(observe) is True and observed
