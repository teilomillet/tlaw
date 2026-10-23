"""Grade comparison with Python and math, never with another tlaw comparison."""

import math
import os

import tlaw
from tlaw.audit import TARGET_ENV
from tlaw.target import load_target


subject = load_target(os.environ.get(TARGET_ENV, "implementations:Core"))
Number = tlaw.number(-1_000, 1_000)
Absolute = tlaw.number(0, 10)
Relative = tlaw.number(0, 0.1)


@tlaw.law
def comparison_matches_independent_numeric_reference(
    left: Number, right: Number, within: Absolute, relative: Relative
):
    expected = math.isclose(left, right, abs_tol=within, rel_tol=relative)
    return (
        subject.same(left, right, within=within, relative=relative).matched is expected
    )


@tlaw.law
def booleans_remain_distinct_from_integers(value: bool):
    return subject.same(value, int(value)).matched is False


@tlaw.law
def a_changed_nested_leaf_names_its_location(value: int):
    comparison = subject.same({"values": [value]}, {"values": [value + 1]})
    difference = comparison.difference
    return (
        not comparison.matched
        and difference is not None
        and difference.path == "['values'][0]"
        and difference.left == value
        and difference.right == value + 1
    )
