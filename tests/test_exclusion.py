"""Laws for `tlaw.excludes`: the one spelling of a non-leakage contract.

Born from the anef probe (issue #1, gap G4): the redaction contract is
stated on a nested refusal value, where `secret not in repr(reply)` fails
as a bare `False` and cannot say which fragment leaked or where.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import tlaw
from tlaw import excludes


HOST = "db-prod-07.internal"


def test_a_clean_value_is_a_truthy_exclusion() -> None:
    exclusion = excludes({"status": "technical_failure"}, HOST, "Traceback")

    assert exclusion.matched
    assert bool(exclusion) is True
    assert "none of the 2 forbidden fragments appear" in exclusion.explain()


def test_a_leak_names_the_fragment_and_its_path() -> None:
    reply = {"status": "technical_failure", "issues": [{"detail": f"at {HOST}:5432"}]}

    exclusion = excludes(reply, HOST)

    assert not exclusion.matched
    assert f"forbidden fragment '{HOST}' found at value['issues'][0]['detail']" in (
        exclusion.explain()
    )


def test_a_leaked_map_key_is_found_because_a_key_is_just_as_exposed() -> None:
    exclusion = excludes({HOST: "unreachable"}, HOST)

    assert not exclusion.matched
    assert f"value.key('{HOST}')" in exclusion.explain()


def test_search_descends_into_lists_tuples_and_sets() -> None:
    assert not excludes([("a", {HOST})], HOST).matched
    assert excludes([("a", {"safe"})], HOST).matched


def test_a_clean_cycle_is_traversed_once_and_reported_clean() -> None:
    reply: list[object] = []
    reply.append(reply)

    assert excludes(reply, HOST).matched


def test_a_leak_after_a_cycle_is_still_found() -> None:
    reply: list[object] = []
    reply.extend([reply, {"detail": f"connect {HOST}"}])

    exclusion = excludes(reply, HOST)

    assert not exclusion.matched
    assert "value[1]['detail']" in exclusion.explain()


def test_a_cycle_through_a_dataclass_is_safe_and_searchable() -> None:
    @dataclass
    class Node:
        next: Node | None = None
        detail: str = "safe"

    clean = Node()
    clean.next = clean
    leaky = Node(detail=f"connect {HOST}")
    leaky.next = leaky

    assert excludes(clean, HOST).matched
    assert not excludes(leaky, HOST).matched


def test_a_dataclass_is_descended_into_so_the_leak_is_located_by_field() -> None:
    """anef's failures are nested dataclasses; a bare repr locates nothing."""

    @dataclass(frozen=True)
    class Failure:
        issues: tuple[str, ...]

    @dataclass(frozen=True)
    class Err:
        error: Failure

    exclusion = excludes(Err(Failure((f"connect {HOST}",))), HOST)

    assert not exclusion.matched
    assert "found at value.error.issues[0]" in exclusion.explain()


def test_a_masking_repr_is_refused_rather_than_reported_clean() -> None:
    """The failure mode that matters: this contract must never fail open.

    Pydantic's `SecretStr` reprs as `**********`. Searching that repr would
    report "clean" about a value that leaks the moment it is serialized,
    and a redaction law passing for that reason is worse than no law.
    """

    class SecretStr:
        def __init__(self, value: str) -> None:
            self._value = value

        def __repr__(self) -> str:
            return "SecretStr('**********')"

    with pytest.raises(TypeError) as refusal:
        excludes(SecretStr(HOST), HOST)

    assert "cannot establish what value contains" in str(refusal.value)
    assert "serialized" in str(refusal.value)


def test_an_object_with_a_hand_written_repr_is_refused_at_its_path() -> None:
    class Reply:
        def __init__(self, detail: str) -> None:
            self.detail = detail

        def __repr__(self) -> str:
            return "Reply(detail=<redacted>)"

    with pytest.raises(TypeError) as refusal:
        excludes({"body": [Reply(f"connect {HOST}")]}, HOST)

    assert "value['body'][0]" in str(refusal.value)


def test_the_serialized_form_the_refusal_recommends_finds_the_leak() -> None:
    """The advice in the refusal must actually work."""
    import json

    assert not excludes(json.dumps({"detail": f"connect {HOST}"}), HOST).matched


def test_an_enum_is_searched_through_its_name_and_value() -> None:
    from enum import Enum

    class Code(str, Enum):
        SAFE = "profile_unavailable"
        LEAKY = f"at {HOST}"

    assert excludes(Code.SAFE, HOST).matched
    assert not excludes(Code.LEAKY, HOST).matched


def test_numbers_and_none_are_readable_leaves() -> None:
    assert excludes(
        {"count": 3, "ratio": 1.5, "missing": None, "ok": True}, HOST
    ).matched


def test_bytes_and_str_fragments_are_not_bridged_by_a_guessed_encoding() -> None:
    assert not excludes(b"connect to " + HOST.encode(), HOST.encode()).matched
    assert excludes(b"connect to " + HOST.encode(), HOST).matched
    assert excludes("connect to " + HOST, HOST.encode()).matched


def test_an_empty_or_missing_fragment_is_refused_as_vacuous() -> None:
    with pytest.raises(TypeError):
        excludes("anything")
    with pytest.raises(ValueError):
        excludes("anything", "")
    with pytest.raises(TypeError):
        excludes("anything", 7)


def test_a_law_returning_an_exclusion_fails_with_the_leak_located() -> None:
    @tlaw.law
    def the_reply_never_names_the_host(value: int):
        return excludes({"issues": [f"{value} at {HOST}"]}, HOST)

    with pytest.raises(AssertionError) as failure:
        the_reply_never_names_the_host()

    assert "forbidden fragment" in str(failure.value)
    assert "value['issues'][0]" in str(failure.value)


def test_excludes_composes_with_and_then_after_a_return_branch() -> None:
    """The two halves of a redaction law keep separate explanations."""

    @dataclass(frozen=True)
    class Err:
        error: str

    def lookup(leaky: bool) -> Err:
        return Err(f"connect {HOST}" if leaky else "profile_unavailable")

    clean = tlaw.returns(Err, lookup, False).and_then(
        lambda failure: excludes(failure, HOST)
    )
    leaked = tlaw.returns(Err, lookup, True).and_then(
        lambda failure: excludes(failure, HOST)
    )

    assert bool(clean) is True
    assert bool(leaked) is False
    assert "forbidden fragment" in leaked.explain()
