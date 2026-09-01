"""Self-laws for tlaw's typed replay-value boundary."""

from __future__ import annotations

import math

import tlaw
from hypothesis import strategies as st
from tlaw.replay import EvidenceEncodingError, decode_value, encode_value


ReplayValues = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=12)
    | st.binary(max_size=12),
    lambda children: (
        st.lists(children, max_size=4)
        | st.lists(children, max_size=4).map(tuple)
        | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=16,
)


@tlaw.law("every supported typed replay value round-trips", value=ReplayValues)
def every_supported_value_round_trips(value) -> bool:
    decoded = decode_value(encode_value(value))
    return decoded == value and type(decoded) is type(value)


@tlaw.law(
    "non-finite floats retain their explicit replay identity",
    value=st.sampled_from([math.nan, math.inf, -math.inf]),
)
def non_finite_floats_retain_their_identity(value: float) -> bool:
    decoded = decode_value(encode_value(value))
    if math.isnan(value):
        return isinstance(decoded, float) and math.isnan(decoded)
    return decoded == value and type(decoded) is float


@tlaw.law(
    "sets and frozensets round-trip independently of iteration order",
    values=st.sets(st.integers(-20, 20), max_size=8),
)
def unordered_values_round_trip(values: set[int]) -> bool:
    mutable = decode_value(encode_value(values))
    frozen = decode_value(encode_value(frozenset(values)))
    return (
        mutable == values
        and type(mutable) is set
        and frozen == frozenset(values)
        and type(frozen) is frozenset
    )


@tlaw.law(
    "cyclic containers are refused instead of flattened",
    value=st.integers(),
)
def cyclic_containers_are_explicitly_unreplayable(value: int) -> bool:
    cyclic: list[object] = [value]
    cyclic.append(cyclic)
    try:
        encode_value(cyclic)
    except EvidenceEncodingError as error:
        return "cyclic containers are not replayable" in str(error)
    return False


@tlaw.law(
    "malformed typed replay values are rejected",
    encoded=st.sampled_from(
        [
            None,
            {},
            {"type": "unknown"},
            {"type": "bool", "value": 1},
            {"type": "int", "value": 1},
            {"type": "list", "items": "not a list"},
            {"type": "dict", "entries": ["not an entry"]},
        ]
    ),
)
def malformed_typed_values_are_rejected(encoded) -> bool:
    try:
        decode_value(encoded)
    except tlaw.ManifestError:
        return True
    return False
