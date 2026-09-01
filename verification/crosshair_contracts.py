"""Independent symbolic contracts for tlaw's pure value boundaries.

These use CrossHair's assert-contract mode deliberately: verification has no
runtime dependency and does not ask tlaw to certify its own oracle.
"""

from __future__ import annotations

from tlaw.comparison import same
from tlaw.exclusion import excludes
from tlaw.replay import decode_value, encode_value


def exact_integer_comparison_is_reflexive(value: int) -> None:
    assert True

    result = same(value, value)

    assert result.matched


def booleans_never_compare_as_integer_counts(value: bool) -> None:
    assert True

    result = same(value, int(value))

    assert not result.matched


def present_text_is_always_found(value: str, fragment: str) -> None:
    assert len(value) <= 8 and 0 < len(fragment) <= 4
    assert fragment in value

    result = excludes(value, fragment)

    assert not result.matched


def text_replay_encoding_round_trips(value: str) -> None:
    assert True

    decoded = decode_value(encode_value(value))

    assert decoded == value and type(decoded) is str
