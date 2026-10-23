"""The shared protocol behind tlaw's claim results.

``Comparison``, ``Refusal``, ``Outcome``, and ``Exclusion`` answer the same
question in four different shapes: did the claim match, and if not, what
happened? They must behave identically as claims — truthy exactly when
matched, failing through one ``law failed: ...`` envelope, and chaining a
second claim only after a match — while keeping their own fields,
constructors, and explanations.

This module holds the parts that are genuinely shared; each public class
keeps its own dataclass fields, ``explain`` text, and ``and_then``
signature. It is internal: nothing here is exported from ``tlaw``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


_REPR_LIMIT = 200


def _short(value: Any) -> str:
    """Render a value for one failure line, truncating huge reprs."""
    text = repr(value)
    if len(text) <= _REPR_LIMIT:
        return text
    return f"{text[:_REPR_LIMIT]}... ({len(text)} characters)"


def _indent(text: str) -> str:
    """Indent every line of a failure explanation under the law line."""
    return "\n".join(f"  {line}" for line in text.splitlines())


class Claim(ABC):
    """The shared behavior of tlaw's claim results.

    A claim is truthy exactly when it matched. A failed claim renders its
    own explanation, and ``check`` raises it under the same failure
    envelope every claim uses; a matched claim raises nothing.
    """

    @property
    @abstractmethod
    def matched(self) -> bool:
        """True when the claim's condition was satisfied."""

    @abstractmethod
    def explain(self) -> str:
        """Render what happened as readable text."""

    def __bool__(self) -> bool:
        return self.matched

    def check(self, description: str = "law") -> None:
        """Raise the failure with the claim's explanation, or do nothing."""
        if self.matched:
            return
        raise AssertionError(f"law failed: {description}\n{_indent(self.explain())}")

    def _continue_claim(self, observe: Callable[..., Any], *evidence: Any) -> Any:
        """Keep this failed claim, or state the next claim after a match."""
        if not self.matched:
            return self
        return observe(*evidence)


def _capture_call(
    call: Callable[[], Any],
    *,
    handled: type[BaseException] | tuple[type[BaseException], ...],
) -> tuple[bool, Any, BaseException | None]:
    """Perform one call and return ``(completed, returned, raised)``.

    Only the exception types the caller names are retained as evidence; a
    non-``Exception`` exit the claim did not name propagates, so tlaw never
    swallows an interrupt as the outcome of a contract that did not ask for
    it.
    """
    try:
        returned = call()
    except handled as error:
        return False, None, error
    return True, returned, None
