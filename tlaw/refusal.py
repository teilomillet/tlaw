"""One explicit exception contract for laws: ``tlaw.raises``.

What
----
``raises(ValueError, pack, lengths, budget)`` states that calling
``pack(lengths, budget)`` must raise ``ValueError`` (or a subclass). It
returns a ``Refusal``: truthy when the expected exception was raised, and
carrying what actually happened — the wrong exception, or the value the
call returned instead — for the failure message.

Why
---
The law-first experiment (docs/law-first-experiment.md) wrote the claim
"an oversized sequence is refused" as try/except boilerplate, and when
such a law fails the output says only ``False`` — it cannot distinguish
"no exception" from "the wrong exception" from "the wrong message". An
error contract is one idea and deserves one visible spelling, with a
failure report that names the actual outcome. The example was accepted as
payment under the interface-budget rule.

How
---
- The claim is about the *call*, so ``raises`` receives the callable and
  its arguments rather than a pre-raised exception: tlaw performs the
  call, observes the outcome, and retains it.
- A subclass of the expected type counts as refused, matching ordinary
  ``except`` semantics.
- Any other ``Exception`` is captured as evidence (type and message; the
  traceback is traded for a one-line explanation). ``KeyboardInterrupt``
  and other non-``Exception`` exits propagate — tlaw never swallows an
  interrupt as evidence.
- ``expected`` must be an ``Exception`` subclass; anything else is
  rejected at the call site rather than producing a claim that can never
  be checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


_REPR_LIMIT = 200


def _short(value: Any) -> str:
    text = repr(value)
    if len(text) <= _REPR_LIMIT:
        return text
    return f"{text[:_REPR_LIMIT]}... ({len(text)} characters)"


@dataclass(frozen=True)
class Refusal:
    """The observed outcome of a call that a law says must raise.

    Exactly one of three things happened, and the report names it:
    the expected exception was raised (``matched``), a different
    exception was raised (``raised`` holds it), or the call returned a
    value (``returned`` holds it).
    """

    expected: type[Exception]
    raised: BaseException | None = None
    returned: Any = field(default=None, repr=False)
    completed: bool = False  # True when the call returned instead of raising

    @property
    def matched(self) -> bool:
        return self.raised is not None and isinstance(self.raised, self.expected)

    def __bool__(self) -> bool:
        return self.matched

    def explain(self) -> str:
        if self.matched:
            return f"the call raised {type(self.raised).__name__} as required"
        if self.completed:
            return (
                f"expected {self.expected.__name__}, but the call returned "
                f"{_short(self.returned)} instead of raising"
            )
        return (
            f"expected {self.expected.__name__}, but the call raised "
            f"{type(self.raised).__name__}: {self.raised}"
        )

    def check(self, description: str = "law") -> None:
        """Raise the failure with the actual outcome named, or do nothing."""
        if self.matched:
            return
        raise AssertionError(f"law failed: {description}\n  {self.explain()}")


def raises(
    expected: type[Exception],
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Refusal:
    """State that calling ``function(*args, **kwargs)`` raises ``expected``.

    Return the resulting ``Refusal`` directly from a law — the failure
    message then names what actually happened — or use it in a plain
    ``assert``.
    """
    if not (isinstance(expected, type) and issubclass(expected, Exception)):
        raise TypeError(
            "raises() needs an Exception subclass as its expected type; "
            f"got {expected!r}"
        )
    try:
        returned = function(*args, **kwargs)
    except Exception as error:  # non-Exception exits propagate deliberately
        return Refusal(expected=expected, raised=error)
    return Refusal(expected=expected, returned=returned, completed=True)
