"""Typed replay values are preserved, with wire fixtures outside the encoder."""

import math
import os

from hypothesis import strategies as st

import tlaw
from tlaw.audit import TARGET_ENV
from tlaw.target import load_target


subject = load_target(os.environ.get(TARGET_ENV, "implementations:Core"))
scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(), st.text(), st.binary()
)
containers = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.tuples(children, children),
        st.dictionaries(st.text(max_size=6), children, max_size=4),
    ),
    max_leaves=12,
)


def preserved(before: object, after: object) -> bool:
    """Plain Python reference: equality alone confuses bool/int and tuple/list."""
    if type(before) is not type(after):
        return False
    if isinstance(before, float):
        if math.isnan(before):
            return math.isnan(after)
        return before == after and (
            before != 0 or math.copysign(1, before) == math.copysign(1, after)
        )
    if isinstance(before, list | tuple):
        return len(before) == len(after) and all(
            preserved(left, right) for left, right in zip(before, after)
        )
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(
            preserved(value, after[key]) for key, value in before.items()
        )
    return before == after


@tlaw.law("scalar replay preserves value and type", value=scalars)
def round_trip_preserves_scalar_value_and_type(value):
    return preserved(value, subject.decode(subject.encode(value)))


@tlaw.law("nested replay preserves container structure", value=containers)
def round_trip_preserves_container_structure(value):
    return preserved(value, subject.decode(subject.encode(value)))


@tlaw.law(
    "set replay preserves its kind and elements",
    values=st.sets(st.integers(), max_size=5),
)
def round_trip_preserves_sets(values):
    for value in (values, frozenset(values)):
        if not preserved(value, subject.decode(subject.encode(value))):
            return False
    return True


@tlaw.law
def integer_wire_format_is_explicit(value: int):
    return subject.encode(value) == {"type": "int", "value": str(value)}


@tlaw.law
def integer_wire_decoding_is_explicit(value: int):
    return preserved(value, subject.decode({"type": "int", "value": str(value)}))
