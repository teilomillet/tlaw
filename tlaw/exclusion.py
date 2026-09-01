"""One explicit non-leakage contract for laws: ``tlaw.excludes``.

What
----
``excludes(reply, hostname, "Traceback")`` states that no forbidden
fragment appears anywhere in ``reply`` — including inside nested
dictionaries, lists, and tuples. It returns an ``Exclusion``: truthy when
nothing forbidden was found, and naming the first fragment it did find and
where, for the failure message.

Why
---
Redaction is a security contract, and the anef probe states it on values
that are not flat strings: a refusal reply is a nested structure, and an
adapter's hostname or database message could surface at any depth in it
(issue #1, gap G4). Written by hand the claim becomes
``secret not in repr(reply)``, which fails as a bare ``False`` — it cannot
say which fragment leaked or where — and quietly depends on every nested
value having a faithful ``repr``.

How
---
- The search is structural, matching ``same``: dictionaries are searched
  through **both keys and values**, because a leaked identifier is just as
  dangerous as a map key, and lists and tuples are searched item by item.
- Strings and bytes are searched by containment. A ``bytes`` fragment is
  looked for in ``bytes``, a ``str`` fragment in ``str``; tlaw does not
  guess an encoding to compare across the two.
- A dataclass is descended into by declared field name, so the leak is
  located (``value.error.issues[0]``) instead of collapsing into one repr
  of the whole failure. An ``Enum`` is searched through its name and value.
- **A value whose content tlaw cannot establish is refused, not guessed.**
  An earlier version rendered unknown objects with ``repr`` and searched
  the text. That fails *open*: a masking ``repr`` — Pydantic's
  ``SecretStr('**********')``, or any hand-written ``__repr__`` that omits
  detail — makes ``excludes`` report "clean" about a value that leaks the
  moment it is serialized. A redaction law that passes for that reason is
  worse than no law, so the guess is gone. tlaw searches only what it can
  read faithfully: text, bytes, the primitives whose ``repr`` the language
  itself defines, and containers/dataclasses/enums built from those.
- The refusal names the fix, and the fix is also the more correct claim:
  pass the value **as it leaves the system** — the serialized reply — since
  that, not a ``repr``, is what a client actually receives.
- An empty fragment is refused at the call site: it appears in everything,
  so the law could never fail and would be vacuous.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any


_REPR_LIMIT = 200


def _short(value: Any) -> str:
    text = repr(value)
    if len(text) <= _REPR_LIMIT:
        return text
    return f"{text[:_REPR_LIMIT]}... ({len(text)} characters)"


@dataclass(frozen=True)
class Leak:
    """The first forbidden fragment found, and the path it was found at."""

    path: str
    fragment: Any
    found_in: Any


@dataclass(frozen=True)
class Exclusion:
    """The result of ``excludes``: truthy when nothing forbidden was found.

    Returned instead of a bare bool so a failing law can name the fragment
    that leaked and the exact position it leaked at, rather than reporting
    ``False`` about a large nested reply.
    """

    value: Any
    forbidden: tuple[Any, ...]
    leak: Leak | None

    @property
    def matched(self) -> bool:
        return self.leak is None

    def __bool__(self) -> bool:
        return self.matched

    def explain(self) -> str:
        if self.leak is None:
            count = len(self.forbidden)
            noun = "fragment" if count == 1 else "fragments"
            return f"none of the {count} forbidden {noun} appear in the value"
        where = self.leak
        lines = [
            f"forbidden fragment {_short(where.fragment)} found at value{where.path}:",
            f"  {_short(where.found_in)}",
        ]
        if where.path:
            lines.append(f"value: {_short(self.value)}")
        return "\n".join(lines)

    def check(self, description: str = "law") -> None:
        """Raise the failure with the leak located, or do nothing."""
        if self.leak is None:
            return
        indented = "\n".join(f"  {line}" for line in self.explain().splitlines())
        raise AssertionError(f"law failed: {description}\n{indented}")


# The leaves tlaw can read faithfully: text and bytes by containment, and
# the primitives whose ``repr`` the language itself defines, which
# therefore cannot conceal a fragment. Anything outside this set is
# refused rather than guessed.
_READABLE_LEAVES = (str, bytes, bytearray, type(None), bool, int, float, complex)


def _contains(haystack: Any, fragment: Any) -> bool:
    """True when one readable leaf carries the fragment.

    ``str`` and ``bytes`` are compared only with their own kind: guessing
    an encoding to bridge them would either invent a leak or miss one, and
    both are worse than making the author state which they mean.
    """
    if isinstance(fragment, bytes):
        if isinstance(haystack, bytes | bytearray):
            return fragment in haystack
        return False
    if isinstance(haystack, str):
        return fragment in haystack
    if isinstance(haystack, bytes | bytearray):
        return False
    return fragment in repr(haystack)


def _refuse_unreadable(value: Any, path: str) -> None:
    """Refuse a value whose content tlaw cannot establish.

    Failing loudly here is the whole point: the alternative is a passing
    redaction law that proved nothing, because it searched a ``repr`` the
    object was free to make up.
    """
    raise TypeError(
        f"excludes() cannot establish what value{path} contains: "
        f"{type(value).__name__} is not text, bytes, a number, an enum, a "
        "dataclass, or a container of those, and its repr is free to hide "
        "the very fragment being looked for (SecretStr renders as "
        "'**********'). Pass the value as it leaves the system — the "
        "serialized reply — which is also what the contract is really about."
    )


def _first_leak(
    value: Any,
    forbidden: tuple[Any, ...],
    path: str,
    visited: set[int],
) -> Leak | None:
    descends = isinstance(
        value, Enum | dict | list | tuple | set | frozenset
    ) or is_dataclass(value)
    if descends:
        identity = id(value)
        if identity in visited:
            return None
        visited.add(identity)

    if isinstance(value, Enum):
        # An enum is a name plus a value, both of which reach output.
        # A str/bytes enum is also its own value, so it is searched as
        # text below and this branch only adds the name.
        for attribute in ("name", "value"):
            found = _first_leak(
                getattr(value, attribute),
                forbidden,
                f"{path}.{attribute}",
                visited,
            )
            if found is not None:
                return found
        return None

    if is_dataclass(value) and not isinstance(value, type):
        # A dataclass declares its fields, so the leak can be located by
        # name instead of collapsing into one repr of the whole value —
        # and, unlike an arbitrary object, what it holds is knowable.
        for member in fields(value):
            found = _first_leak(
                getattr(value, member.name),
                forbidden,
                f"{path}.{member.name}",
                visited,
            )
            if found is not None:
                return found
        return None

    if isinstance(value, dict):
        for key, item in value.items():
            # Keys are searched too: a leaked identifier used as a map key
            # is exactly as exposed as one used as a value.
            found = _first_leak(key, forbidden, f"{path}.key({key!r})", visited)
            if found is not None:
                return found
            found = _first_leak(item, forbidden, f"{path}[{key!r}]", visited)
            if found is not None:
                return found
        return None

    if isinstance(value, list | tuple | set | frozenset):
        for index, item in enumerate(value):
            found = _first_leak(item, forbidden, f"{path}[{index}]", visited)
            if found is not None:
                return found
        return None

    if not isinstance(value, _READABLE_LEAVES):
        _refuse_unreadable(value, path)

    for fragment in forbidden:
        if _contains(value, fragment):
            return Leak(path=path, fragment=fragment, found_in=value)
    return None


def excludes(value: Any, /, *forbidden: Any) -> Exclusion:
    """State that no forbidden fragment appears anywhere in ``value``.

    Return the resulting ``Exclusion`` directly from a law — the failure
    then names the fragment and its path — or use it in a plain ``assert``:

        return tlaw.excludes(reply, hostname, "Traceback", "password")

    Searching descends into dictionaries (keys and values), lists, tuples,
    sets, dataclasses, and enums. A value whose content tlaw cannot
    establish — an object with a hand-written or masking ``repr`` — is
    **refused**, not guessed, because guessing makes this contract fail
    open. Pass the serialized form in that case.

    An empty fragment is refused, because it appears in every value and the
    law could never fail.
    """
    if not forbidden:
        raise TypeError(
            "excludes() needs at least one forbidden fragment; a claim with "
            "nothing forbidden could never fail"
        )
    for fragment in forbidden:
        if not isinstance(fragment, str | bytes):
            raise TypeError(
                "excludes() forbidden fragments must be str or bytes; got "
                f"{type(fragment).__name__}"
            )
        if not fragment:
            raise ValueError(
                "excludes() was given an empty fragment; it appears in every "
                "value, so the law could never fail"
            )
    return Exclusion(
        value=value,
        forbidden=forbidden,
        leak=_first_leak(value, forbidden, "", set()),
    )


__all__ = ["Exclusion", "Leak", "excludes"]
