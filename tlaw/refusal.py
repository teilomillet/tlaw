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
- An ``async`` target is awaited. Calling an ``async def`` only builds a
  coroutine, so a contract stated about the call used to observe a
  returned coroutine instead of the outcome — the work had not happened
  yet. tlaw runs it to completion and reports the real outcome, and says
  so in the explanation.
- ``expected`` may be any ``BaseException`` subclass, because real control
  flow lives outside ``Exception``: ``asyncio.CancelledError`` is the case
  that earned it (issue #1, gap G5). Anything that is not an exception
  type is rejected at the call site rather than producing a claim that can
  never be checked.
- Any other ``Exception`` is captured as evidence (type and message; the
  traceback is traded for a one-line explanation). A non-``Exception``
  exit the law did not name — ``KeyboardInterrupt`` under a law about
  ``ValueError`` — still propagates, so tlaw never swallows an interrupt
  as evidence for a contract that did not ask for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .awaiting import awaited, isawaitable, refuse_running_loop
from .claims import Claim, _capture_call, _short


@dataclass(frozen=True)
class Refusal(Claim):
    """The observed outcome of a call that a law says must raise.

    Exactly one of three things happened, and the report names it:
    the expected exception was raised (``matched``), a different
    exception was raised (``raised`` holds it), or the call returned a
    value (``returned`` holds it).
    """

    expected: type[BaseException]
    raised: BaseException | None = None
    returned: Any = field(default=None, repr=False)
    completed: bool = False  # True when the call returned instead of raising
    awaited: bool = False  # True when tlaw ran an async target to completion

    @property
    def matched(self) -> bool:
        return self.raised is not None and isinstance(self.raised, self.expected)

    def explain(self) -> str:
        # Whether tlaw awaited the target changes what "the call" means, so
        # the report names it rather than leaving the reader to assume.
        call = "the awaited call" if self.awaited else "the call"
        if self.matched:
            return f"{call} raised {type(self.raised).__name__} as required"
        if self.completed:
            return (
                f"expected {self.expected.__name__}, but {call} returned "
                f"{_short(self.returned)} instead of raising"
            )
        return (
            f"expected {self.expected.__name__}, but {call} raised "
            f"{type(self.raised).__name__}: {self.raised}"
        )

    def and_then(self, observe: Callable[[BaseException], Any]) -> Any:
        """State a second claim about the exception this call actually raised.

        A refusal contract is often two claims: *that* the call refused,
        and *what* the surfaced failure says — Law 4 of issue #1 requires
        both server attribution and a redacted message. Writing them with
        ``and`` collapses the first into a bare boolean and loses its
        explanation, so ``and_then`` keeps whichever claim failed::

            return tlaw.raises(RuntimeError, verify, request).and_then(
                lambda error: tlaw.excludes(str(error), secret)
            )

        When the expected exception was not raised, this refusal is
        returned unchanged and reports the actual outcome. Otherwise
        ``observe`` receives the raised exception and its own result — a
        ``Comparison``, another ``Refusal``, an ``Outcome``, or a bool —
        becomes the law's result.
        """
        return self._continue_claim(observe, self.raised)


def raises(
    expected: type[BaseException],
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Refusal:
    """State that calling ``function(*args, **kwargs)`` raises ``expected``.

    Return the resulting ``Refusal`` directly from a law — the failure
    message then names what actually happened — or use it in a plain
    ``assert``.

    An ``async`` target is awaited, so the same spelling states a
    cancellation contract on a coroutine function::

        return tlaw.raises(asyncio.CancelledError, resolve, request, resolver)
    """
    if not (isinstance(expected, type) and issubclass(expected, BaseException)):
        raise TypeError(
            f"raises() needs an exception type as its expected type; got {expected!r}"
        )
    outcome = _observed(expected, lambda: function(*args, **kwargs), awaited_call=False)
    if outcome.completed and isawaitable(outcome.returned):
        # An async target has not done its work yet: calling it only built
        # a coroutine. Run it, and report the outcome of that run.
        refuse_running_loop(outcome.returned)
        return _observed(expected, lambda: awaited(outcome.returned), awaited_call=True)
    return outcome


def _observed(
    expected: type[BaseException],
    call: Callable[[], Any],
    *,
    awaited_call: bool,
) -> Refusal:
    """Perform one call and retain exactly what it did.

    A named ``BaseException`` such as ``CancelledError`` and every other
    ``Exception`` are retained as evidence; a non-``Exception`` exit the law
    did not name propagates, so an interrupt is never captured as a result.
    """
    completed, returned, raised = _capture_call(call, handled=(expected, Exception))
    return Refusal(
        expected=expected,
        raised=raised,
        returned=returned,
        completed=completed,
        awaited=awaited_call,
    )
