"""Immutable categorical paths used by tlaw's authoring interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar


Source = TypeVar("Source")
Middle = TypeVar("Middle")
Target = TypeVar("Target")


@dataclass(frozen=True)
class Box(Generic[Source, Target]):
    """One named transformation inside an arrow."""

    name: str
    function: Callable[[Source], Target]


@dataclass(frozen=True)
class Arrow(Generic[Source, Target]):
    """An immutable sequence of transformations."""

    boxes: tuple[Box[Any, Any], ...] = ()
    _identity_name: str = field(default="identity", compare=False, repr=False)

    @property
    def name(self) -> str:
        if not self.boxes:
            return self._identity_name
        return " >> ".join(box.name for box in self.boxes)

    def __call__(self, value: Source) -> Target:
        result: Any = value
        for box in self.boxes:
            result = box.function(result)
        return result

    def then(self, following: Arrow[Target, Middle]) -> Arrow[Source, Middle]:
        """Compose by concatenating immutable step sequences."""
        if not self.boxes:
            return following
        if not following.boxes:
            return self
        return Arrow(boxes=self.boxes + following.boxes)

    def __rshift__(self, following: Arrow[Target, Middle]) -> Arrow[Source, Middle]:
        return self.then(following)


def arrow(
    function: Callable[[Source], Target], *, name: str | None = None
) -> Arrow[Source, Target]:
    """Turn an ordinary one-argument Python function into a named arrow."""
    arrow_name = name or getattr(function, "__name__", type(function).__name__)
    if not arrow_name:
        raise ValueError("an arrow name cannot be empty")
    return Arrow(boxes=(Box(name=arrow_name, function=function),))


def identity(name: str = "identity") -> Arrow[Source, Source]:
    """Return the empty, do-nothing arrow for any value."""
    return Arrow(_identity_name=name)


def assert_equivalent(
    left: Arrow[Source, Target],
    right: Arrow[Source, Target],
    value: Source,
) -> None:
    """Assert that two parallel arrows produce the same observable result."""
    left_result = left(value)
    right_result = right(value)
    if left_result != right_result:
        raise AssertionError(
            f"paths differ for {value!r}:\n"
            f"  {left.name} -> {left_result!r}\n"
            f"  {right.name} -> {right_result!r}"
        )
