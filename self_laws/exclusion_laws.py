"""Self-laws for tlaw's nested exclusion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import tlaw
from tlaw.exclusion import excludes


SafeLeaves = tlaw.one_of([None, False, 0, 1.5, "", "safe", b"safe"])
SafeTrees = tlaw.recursive(
    SafeLeaves,
    lambda child: tlaw.list_of(child, max_size=3),
    max_leaves=12,
)
SmallInt = tlaw.integer(-100, 100)
SECRET = "db-prod.internal"


@tlaw.law
def every_generated_safe_tree_is_clean(value: SafeTrees):
    return excludes(value, SECRET)


@tlaw.law
def a_nested_leak_is_found_at_its_first_path(value: SmallInt) -> bool:
    reply = {"status": "failed", "issues": [{"detail": f"{value}: {SECRET}"}]}
    result = excludes(reply, SECRET)
    return (
        not result.matched
        and result.leak is not None
        and result.leak.path == "['issues'][0]['detail']"
        and f"forbidden fragment '{SECRET}'" in result.explain()
        and f"value: {reply!r}" in result.explain()
    )


@tlaw.law
def mapping_keys_are_as_exposed_as_values(value: SmallInt) -> bool:
    result = excludes({f"{SECRET}-{value}": "unreachable"}, SECRET)
    return not result.matched and ".key(" in result.explain()


@tlaw.law
def cycles_are_traversed_once_without_hiding_later_leaks(value: SmallInt) -> bool:
    # Termination is part of this cycle contract; recursion is a stable
    # law refutation even when the stack limit is reached at different sites.
    try:
        clean: list[object] = []
        clean.append(clean)
        if not excludes(clean, SECRET).matched:
            return False
        leaky: list[object] = []
        leaky.extend([leaky, {"detail": f"{value}: {SECRET}"}])
        result = excludes(leaky, SECRET)
        return not result.matched and "value[1]['detail']" in result.explain()
    except RecursionError:
        return False


@tlaw.law
def dataclass_cycles_are_safe_and_searchable(value: SmallInt) -> bool:
    # Termination is part of this cycle contract; recursion is a stable
    # law refutation even when the stack limit is reached at different sites.
    try:

        @dataclass
        class Node:
            next: Node | None = None
            detail: str = "safe"

        clean = Node()
        clean.next = clean
        leaky = Node(detail=f"{value}: {SECRET}")
        leaky.next = leaky
        return excludes(clean, SECRET).matched and not excludes(leaky, SECRET).matched
    except RecursionError:
        return False


@tlaw.law
def text_and_bytes_never_guess_an_encoding(value: SmallInt) -> bool:
    text = f"{value}: {SECRET}"
    encoded = text.encode()
    return (
        not excludes(text, SECRET).matched
        and excludes(text, SECRET.encode()).matched
        and not excludes(encoded, SECRET.encode()).matched
        and excludes(encoded, SECRET).matched
    )


@tlaw.law
def enum_names_and_values_are_both_visible(value: SmallInt) -> bool:
    class Code(str, Enum):
        SAFE = "profile_unavailable"
        LEAKY = f"{value}: {SECRET}"

    return (
        excludes(Code.SAFE, SECRET).matched
        and not excludes(Code.LEAKY, SECRET).matched
        and not excludes(Code.LEAKY, "LEAKY").matched
    )


@tlaw.law
def invalid_or_vacuous_forbidden_sets_are_refused(value: SmallInt) -> bool:
    cases = [(), ("",), (value,)]
    expected = [TypeError, ValueError, TypeError]
    for forbidden, error_type in zip(cases, expected):
        try:
            excludes("anything", *forbidden)
        except error_type:
            continue
        return False
    return True


@tlaw.law
def opaque_objects_are_refused_instead_of_assumed_clean(value: SmallInt) -> bool:
    class Masked:
        def __repr__(self) -> str:
            return "Masked('********')"

    try:
        excludes({"body": [Masked()]}, f"{SECRET}-{value}")
    except TypeError as error:
        return "cannot establish what value['body'][0] contains" in str(error)
    return False


@tlaw.law
def root_and_nested_reports_have_distinct_context(value: SmallInt) -> bool:
    root = excludes(f"{value}: {SECRET}", SECRET).explain().splitlines()
    nested_value = {"detail": f"{value}: {SECRET}"}
    nested = excludes(nested_value, SECRET).explain()
    return (
        len(root) == 2
        and root[0].startswith("forbidden fragment")
        and f"value: {nested_value!r}" in nested
    )


@tlaw.law
def reports_truncate_only_above_the_exact_limit(value: SmallInt) -> bool:
    exact_value = "x" * 198
    long_value = "x" * 199
    exact = excludes(exact_value, "x").explain()
    truncated = excludes(long_value, "x").explain()
    return "... (200 characters)" not in exact and "... (201 characters)" in truncated
