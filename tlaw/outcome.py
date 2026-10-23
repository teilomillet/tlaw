"""One explicit return-branch contract for laws: ``tlaw.returns``.

What
----
``returns(Err, resolve, request, resolver)`` states that calling
``resolve(request, resolver)`` must return an ``Err`` — an instance of the
named type. It returns an ``Outcome``: truthy when that branch came back,
and carrying what actually happened (the other branch's value, or the
exception raised instead) for the failure message.

Why
---
Railway code does not raise. ``interpret`` and ``resolve`` in the anef
probe return ``Result[Value, Failure]``, so every contract about their
failure path had to be written as a bare boolean::

    return isinstance(interpret(payload, limits), Err)

and a failing law then reports only ``False``. It cannot say whether the
call returned ``Ok``, returned something else entirely, or raised. That is
exactly the report quality ``raises`` was built to give exception
contracts (issue #1, gap G2), so ``returns`` is its dual: the same
argument shape, the same retained evidence, for the branch that comes back
as a value instead of as a raise.

How
---
- The claim is about the *call*, so ``returns`` performs it and retains
  the outcome, rather than receiving an already-computed value.
- A subclass of the expected type counts, matching ``isinstance``.
- An ``async`` target is awaited, because calling an ``async def`` only
  builds a coroutine and the branch has not been chosen yet.
- An exception is a failure of this contract, not evidence of it, and is
  reported as what actually happened. A non-``Exception`` exit still
  propagates: a law about ``Err`` never swallows an interrupt.
- ``and_then`` states a second claim about the value that came back —
  which attribution, which codes — without collapsing the first claim into
  a bare boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .awaiting import awaited, isawaitable, refuse_running_loop
from .claims import Claim, _capture_call, _short


@dataclass(frozen=True)
class Outcome(Claim):
    """The observed outcome of a call that a law says must return a branch.

    Exactly one of three things happened, and the report names it: the
    expected type came back (``matched``), a different value came back
    (``returned`` holds it), or the call raised (``raised`` holds it).
    """

    expected: type
    returned: Any = field(default=None, repr=False)
    raised: BaseException | None = None
    completed: bool = False  # True when the call returned instead of raising
    awaited: bool = False  # True when tlaw ran an async target to completion

    @property
    def matched(self) -> bool:
        return self.completed and isinstance(self.returned, self.expected)

    @property
    def value(self) -> Any:
        """The value the call returned, for a further claim about it."""
        return self.returned

    def explain(self) -> str:
        call = "the awaited call" if self.awaited else "the call"
        if self.matched:
            return f"{call} returned {self.expected.__name__} as required"
        if self.completed:
            return (
                f"expected {self.expected.__name__}, but {call} returned "
                f"{_short(self.returned)}"
            )
        return (
            f"expected {self.expected.__name__}, but {call} raised "
            f"{type(self.raised).__name__}: {self.raised}"
        )

    def and_then(self, observe: Callable[[Any], Any]) -> Any:
        """State a second claim about the value this call actually returned.

        A railway contract is usually two claims: *that* the call took the
        failure branch, and *what* that failure says. Writing them with
        ``and`` collapses the first into a bare boolean and loses its
        explanation, so ``and_then`` keeps whichever claim failed::

            return tlaw.returns(Err, resolve, request, resolver).and_then(
                lambda failure: tlaw.same(codes(failure), ("field_unknown",))
            )

        When the expected branch did not come back, this outcome is
        returned unchanged and reports what actually happened. Otherwise
        ``observe`` receives the returned value and its own result — a
        ``Comparison``, an ``Exclusion``, a ``Refusal``, another
        ``Outcome``, or a bool — becomes the law's result.
        """
        return self._continue_claim(observe, self.returned)


def returns(
    expected: type,
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Outcome:
    """State that ``function(*args, **kwargs)`` returns an ``expected`` value.

    Return the resulting ``Outcome`` directly from a law — the failure
    message then names what actually came back — or use it in a plain
    ``assert``. An ``async`` target is awaited, so the same spelling states
    a contract on a coroutine function.
    """
    if not isinstance(expected, type):
        raise TypeError(
            f"returns() needs a type as its expected branch; got {expected!r}"
        )
    if issubclass(expected, BaseException):
        # An exception class here is almost always a raises() contract
        # written with the wrong verb, and returns() would report the
        # confusing "the call raised" for the outcome the author wanted.
        raise TypeError(
            f"returns() expects a returned value, but {expected.__name__} is an "
            "exception type; state an exception contract with tlaw.raises"
        )
    outcome = _observed(expected, lambda: function(*args, **kwargs), awaited_call=False)
    if outcome.completed and isawaitable(outcome.returned):
        # An async target has not chosen a branch yet: calling it only
        # built a coroutine. Run it, and report the outcome of that run.
        refuse_running_loop(outcome.returned)
        return _observed(expected, lambda: awaited(outcome.returned), awaited_call=True)
    return outcome


def _observed(
    expected: type,
    call: Callable[[], Any],
    *,
    awaited_call: bool,
) -> Outcome:
    """Perform one call and retain exactly what it did.

    An ``Exception`` is retained as the failure of this contract. A
    non-``Exception`` exit propagates, so an interrupt is never captured as
    a result.
    """
    completed, returned, raised = _capture_call(call, handled=Exception)
    return Outcome(
        expected=expected,
        returned=returned,
        raised=raised,
        completed=completed,
        awaited=awaited_call,
    )


__all__ = ["Outcome", "returns"]
