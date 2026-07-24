"""Laws for `tlaw.same`: the one comparison a law may own.

These pin the comparison contract: absolute tolerance on numeric leaves
only, NaN never equal, bool strictness, array-likes by value, and both
sides retained with the first difference for the failure message.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tlaw import same


values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**6), max_value=10**6)
    | st.floats(allow_nan=False, allow_infinity=False, width=32)
    | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=4), children, max_size=4)
    ),
    max_leaves=12,
)


@given(value=values)
def test_every_value_is_the_same_as_itself(value: object) -> None:
    comparison = same(value, value)

    assert comparison.matched
    assert bool(comparison) is True


@given(left=values, right=values, within=st.floats(min_value=0, max_value=1e6))
def test_matching_is_symmetric(left: object, right: object, within: float) -> None:
    assert same(left, right, within=within).matched is (
        same(right, left, within=within).matched
    )


@given(
    left=st.floats(allow_nan=False, allow_infinity=False),
    right=st.floats(allow_nan=False, allow_infinity=False),
    within=st.floats(min_value=0, max_value=1e12),
)
def test_within_is_exactly_absolute_tolerance_on_floats(
    left: float, right: float, within: float
) -> None:
    expected = left == right or abs(left - right) <= within
    assert same(left, right, within=within).matched is expected


def test_exact_is_the_default_and_the_failure_explains_itself() -> None:
    comparison = same(0.1 + 0.2, 0.3)

    assert not comparison.matched
    assert "absolute difference" in comparison.explain()
    assert "within=0.0" in comparison.explain()

    assert same(0.1 + 0.2, 0.3, within=1e-12).matched


def test_nan_never_compares_equal_and_says_so() -> None:
    comparison = same(float("nan"), float("nan"), within=1e9)

    assert not comparison.matched
    assert "NaN" in comparison.difference.reason


def test_equal_infinities_match_and_opposite_ones_do_not() -> None:
    assert same(math.inf, math.inf).matched
    assert not same(math.inf, -math.inf).matched


def test_bool_only_compares_with_bool() -> None:
    # True == 1 in Python; a passing law must not hide that type change.
    comparison = same(True, 1)

    assert not comparison.matched
    assert "bool" in comparison.difference.reason
    assert same(True, True).matched


def test_the_first_difference_names_its_path() -> None:
    comparison = same(
        {"advantages": [0.0, [1.0, 2.0]]},
        {"advantages": [0.0, [1.0, 2.5]]},
    )

    assert not comparison.matched
    assert comparison.difference.path == "['advantages'][1][1]"
    assert comparison.difference.left == 2.0
    assert comparison.difference.right == 2.5
    explained = comparison.explain()
    assert "value['advantages'][1][1]" in explained
    # The full values stay available next to the pinpointed difference.
    assert "left:" in explained and "right:" in explained


def test_length_and_key_differences_are_stated_not_guessed() -> None:
    lengths = same([1.0, 2.0], [1.0])
    assert "lengths differ: 2 vs 1" in lengths.difference.reason

    keys = same({"a": 1}, {"b": 1})
    assert "only in left: 'a'" in keys.difference.reason
    assert "only in right: 'b'" in keys.difference.reason


def test_check_raises_with_both_sides_retained() -> None:
    comparison = same([1.0, 2.0], [1.0, 2.5], within=0.1)

    with pytest.raises(AssertionError) as failure:
        comparison.check("advantages match the reference")

    message = str(failure.value)
    assert "law failed: advantages match the reference" in message
    assert "value[1]" in message
    assert "2.0" in message and "2.5" in message


def test_and_then_states_a_second_explanatory_claim_after_values_match() -> None:
    refactored_steps = 0
    baseline_steps = 0

    result = same([], []).and_then(
        lambda: same(
            (refactored_steps, baseline_steps),
            (1, 1),
        )
    )

    assert bool(result) is False
    assert "first difference at value[0]" in result.explain()
    assert "left:  0" in result.explain()
    assert "right: 1" in result.explain()


def test_and_then_keeps_the_first_comparison_and_skips_the_next_claim() -> None:
    observed = False
    comparison = same(["refactored"], ["baseline"])

    def next_claim() -> bool:
        nonlocal observed
        observed = True
        return True

    result = comparison.and_then(next_claim)

    assert result is comparison
    assert observed is False
    assert "'refactored'" in result.explain()
    assert "'baseline'" in result.explain()


def test_and_then_chains_comparisons_without_collapsing_the_failure() -> None:
    result = same(1, 1).and_then(lambda: same(2, 3)).and_then(lambda: same(4, 5))

    assert result.left == 2
    assert result.right == 3
    assert "left:  2" in result.explain()
    assert "right: 3" in result.explain()


def test_within_must_be_a_finite_non_negative_number() -> None:
    with pytest.raises(ValueError):
        same(1.0, 1.0, within=-0.1)
    with pytest.raises(ValueError):
        same(1.0, 1.0, within=math.nan)
    with pytest.raises(TypeError):
        same(1.0, 1.0, within=True)


def test_numpy_arrays_compare_by_value_with_tolerance() -> None:
    np = pytest.importorskip("numpy")

    array = np.array([[0.1, 0.2], [0.3, 0.4]])
    assert same(np.flip(np.flip(array)), array).matched
    assert same(array, [[0.1, 0.2], [0.3, 0.4]], within=1e-12).matched
    assert same(np.float64(0.1) + np.float64(0.2), 0.3, within=1e-12).matched

    shape_mismatch = same(np.zeros(3), np.zeros((3, 1)))
    assert not shape_mismatch.matched

    empty_shape_mismatch = same(np.empty((0, 2)), np.empty((0, 3)))
    assert not empty_shape_mismatch.matched
    assert "array shapes differ" in empty_shape_mismatch.difference.reason

    off_by_more = same(np.array([1.0, 2.0]), np.array([1.0, 2.5]), within=0.1)
    assert not off_by_more.matched
    assert off_by_more.difference.path == "[1]"


def test_unrecognized_equality_results_are_reported_not_guessed() -> None:
    class Weird:
        def __eq__(self, other: object) -> object:
            return [True]  # one value per element, like an array

        __hash__ = None

    comparison = same(Weird(), Weird())

    assert not comparison.matched
    assert "cannot interpret ==" in comparison.difference.reason


@given(
    magnitude=st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
    fraction=st.floats(min_value=0, max_value=1e-10, allow_nan=False),
)
def test_relative_tolerance_scales_with_magnitude(
    magnitude: float, fraction: float
) -> None:
    # A fixed relative error is accepted at every magnitude, which a
    # single absolute tolerance cannot do (see the docstring rationale).
    perturbed = magnitude * (1 + fraction)
    assert same(magnitude, perturbed, relative=1e-9).matched


def test_relative_catches_a_large_value_that_absolute_would_pass() -> None:
    # 0.00000001% off at magnitude 1e12 is a gap of ~0.1: absolute 1e-6
    # rejects it as it should; a loose absolute tolerance would not.
    a = 1e12
    b = 1e12 * (1 + 1e-13)
    assert not same(a, b, within=1e-6).matched
    # The same fractional error passes under relative tolerance, and so
    # does the identical fraction at magnitude 1.0 — consistent judgement.
    assert same(a, b, relative=1e-12).matched
    assert same(1.0, 1.0 * (1 + 1e-13), relative=1e-12).matched


def test_within_and_relative_combine_like_isclose() -> None:
    # Near zero, relative is powerless and within carries the comparison.
    assert same(0.0, 1e-13, relative=1e-9).matched is False
    assert same(0.0, 1e-13, within=1e-12).matched is True
    # Away from zero, relative carries it and within need not be set.
    assert same(1e6, 1e6 + 1e-4, relative=1e-9).matched is True


def test_infinity_never_passes_a_finite_tolerance() -> None:
    # Relative * infinity would be infinity; guarded so it cannot wrongly
    # swallow a finite value.
    assert not same(math.inf, 1e300, relative=1.0).matched
    assert "infinite" in same(math.inf, 1.0, relative=1.0).difference.reason


def test_huge_integers_keep_exact_relative_tolerance_semantics() -> None:
    huge = 10**1000

    # Python integers are arbitrary precision. Comparing them must not
    # crash just because the tolerance implementation normally uses floats.
    assert not same(huge, huge + 1).matched
    assert same(huge, huge + 1, relative=1e-9).matched
    # A broad relative tolerance still means the documented equation;
    # it must not become an automatic pass when float conversion overflows.
    assert not same(huge, -huge, relative=1.0).matched


def test_the_failure_names_both_tolerances_when_relative_is_set() -> None:
    comparison = same(1e12, 1e12 * (1 + 1e-6), within=1e-9, relative=1e-12)

    assert not comparison.matched
    reason = comparison.difference.reason
    assert "within=1e-09" in reason
    assert "relative=1e-12" in reason
    assert "allowance" in reason


def test_relative_must_be_a_finite_non_negative_number() -> None:
    with pytest.raises(ValueError):
        same(1.0, 1.0, relative=-0.1)
    with pytest.raises(ValueError):
        same(1.0, 1.0, relative=math.inf)
    with pytest.raises(TypeError):
        same(1.0, 1.0, relative=True)
