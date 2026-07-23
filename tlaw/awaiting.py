"""Run one awaitable to completion, for laws whose target is ``async``.

What
----
``awaited(value)`` runs an awaitable — a coroutine, a task, or any object
with ``__await__`` — and returns what it produced, propagating whatever it
raised. ``tlaw.raises`` and ``tlaw.returns`` use it so a law about an
``async def`` reads exactly like a law about an ordinary function.

Why
---
An ``async`` function does not do its work when it is called; it returns a
coroutine. A contract stated about the call therefore observed the wrong
thing: ``raises(CancelledError, resolve, request)`` saw a coroutine come
back instead of an exception, and the real outcome never happened at all.
The anef probe hit this on an ``async def resolve`` whose cancellation
contract could not be written without an ``asyncio.run`` shim inside the
law body (issue #1, gap G5).

How
---
- tlaw owns the event loop for the call, because it owns the call. A fresh
  loop per observation keeps the same fresh-start discipline paths already
  have: no state survives from one observed call to the next.
- A law is an ordinary synchronous function, so there is normally no loop
  running. If there is one, tlaw refuses rather than deadlocking or
  silently observing something else — an unrunnable claim is reported, not
  guessed.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


def refuse_running_loop(value: Any = None) -> None:
    """Refuse to await inside a running loop, or do nothing.

    ``value`` is the awaitable an observed call returned. Close a coroutine
    before refusing so the harness error does not also leak an unawaited
    coroutine warning. A contract's evidence must describe the target, so
    "tlaw could not run this at all" has to surface as a harness error rather
    than being captured as "the call raised RuntimeError" — which would read
    as a fact about the code under law.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    if inspect.iscoroutine(value):
        value.close()
    raise RuntimeError(
        "tlaw cannot await this call: an asyncio event loop is already "
        "running in this thread. A law is an ordinary synchronous function, "
        "so tlaw owns the loop for the calls it makes; run the law outside a "
        "running loop."
    )


def awaited(value: Any) -> Any:
    """Run ``value`` to completion and return its result.

    Only call this when ``isawaitable(value)`` is true and
    ``refuse_running_loop(value)`` has already passed. Whatever the awaitable
    raises propagates unchanged, including ``CancelledError``, so a
    cancellation contract stays observable as cancellation.
    """
    return asyncio.run(_completed(value))


async def _completed(value: Any) -> Any:
    """Await any awaitable, not only a coroutine, inside tlaw's own loop."""
    return await value


def isawaitable(value: Any) -> bool:
    """True when a returned value still has to be run to produce an outcome."""
    return inspect.isawaitable(value)


__all__ = ["awaited", "isawaitable"]
