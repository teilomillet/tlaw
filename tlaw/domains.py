"""Readable value domains that compile to Hypothesis strategies.

What
----
``number(low, high)`` declares a finite float between two bounds.
``integer(low, high)`` declares a bounded integer.
``list_of(element, ...)`` declares a list of a domain or plain type.
``depends(builder)`` derives a domain from other generated parameters.
A domain is an ordinary value: assign it a name, use that name as a
parameter annotation, and the bare ``@tlaw.law`` form generates inputs
from it — the retrain probe's syntax candidate, now executable:

    Reward = tlaw.number(0, 1)
    Rewards = tlaw.list_of(Reward, min_size=1)

    @tlaw.law
    def advantages_are_centered(rewards: Rewards):
        return tlaw.same(sum(advantages(rewards)), 0.0, within=1e-9)

Why
---
Probe bottleneck 1: ``list[float]`` honestly includes NaN, infinity, and
1e308, which most application laws do not accept, and writing the honest
strategy by hand cost 55 lines for two laws. A domain states the accepted
inputs in application vocabulary while staying visible in the law's
signature — the domain is part of the contract, so it must be readable
where the law is read (``repr(Reward)`` is ``"number(0, 1)"``, and that
is what ``tlaw check --explain`` prints).

How
---
- ``number`` generates finite floats only: no NaN, no infinity, both
  bounds included. Bounds must themselves be finite numbers with
  ``low <= high`` — an impossible domain is rejected at definition time,
  not discovered as a confusing generation error later.
- ``integer`` generates integers with both bounds included and rejects
  bools, non-integer bounds, and reversed bounds at definition time.
- ``list_of`` accepts a ``Domain`` or a plain type (``int``, ``str``);
  a plain type generates exactly what the bare annotation would, so the
  two spellings never disagree.
- ``depends`` names dependencies through the builder's parameter names;
  tlaw draws those first and builds the dependent domain without filtering.
- Domains used as annotations must be module-level names: annotations
  written as strings (``from __future__ import annotations``) are
  resolved against the law module's globals, which is where a shared
  domain belongs anyway — next to the laws that use it.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy


@dataclass(frozen=True, eq=False)
class Domain:
    """A declared input domain: a description plus one Hypothesis strategy.

    The description is the whole point: it is what a reviewer sees in the
    law's signature and what ``--explain`` prints, so generation is never
    an invisible spec. The strategy is deliberately not part of ``repr``.
    """

    description: str
    strategy: SearchStrategy[Any] = field(repr=False)

    def __repr__(self) -> str:
        return self.description


@dataclass(frozen=True, eq=False)
class DependentDomain:
    """A domain built from other parameters' generated values.

    Valid inputs are often relational: a sequence length must fit a
    budget, a tensor-parallel size must divide a GPU count, and a mask
    must have the same shape as its logprobs. Independent per-parameter
    domains cannot state those, so laws used to narrow their inputs
    inside the body — which worked, but made ``--explain`` describe a
    wider domain than the law exercised (docs/law-first-experiment.md).

    A dependent domain fixes the truthfulness at the source: what is
    declared is what is generated. ``dependencies`` are the names of
    other law parameters, taken from the builder's own parameter names,
    and ``build`` receives their drawn values and returns an ordinary
    ``Domain``.
    """

    description: str
    dependencies: tuple[str, ...]
    build: Callable[..., "Domain"] = field(repr=False)

    def __repr__(self) -> str:
        return self.description

    def resolve(self, values: dict[str, Any]) -> Domain:
        """Build the concrete domain for one set of drawn dependencies."""
        domain = self.build(**{name: values[name] for name in self.dependencies})
        if not isinstance(domain, Domain):
            raise TypeError(
                f"a dependent domain must build a tlaw domain; {self.description} "
                f"returned {type(domain).__name__}"
            )
        return domain


def depends(builder: Callable[..., Domain]) -> DependentDomain:
    """Declare a domain derived from other parameters of the same law.

    The builder's parameter names are the dependencies, matched by name
    against the law's own parameters::

        Budget = tlaw.integer(1, 30)
        Lengths = tlaw.depends(
            lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
        )

        @tlaw.law
        def no_batch_exceeds_the_budget(budget: Budget, lengths: Lengths):
            ...

    tlaw draws the dependencies first and builds this domain from their
    values, so every generated example satisfies the relation by
    construction — no filtering, therefore no vacuous examples, and
    ``--explain`` states the dependency rather than a wider domain the
    law never exercises.
    """
    if not callable(builder):
        raise TypeError("depends() needs a function that returns a tlaw domain")
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError) as error:
        raise TypeError("depends() cannot inspect that builder") from error

    names = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                "a dependent domain must name its dependencies explicitly; "
                "positional-only parameters, *args, and **kwargs cannot be "
                "matched to law parameters"
            )
        names.append(parameter.name)
    if not names:
        raise ValueError(
            "depends() with no parameters declares no dependency; use the "
            "domain directly instead"
        )
    return DependentDomain(
        description=f"depends({', '.join(names)})",
        dependencies=tuple(names),
        build=builder,
    )


def generation_order(
    specifications: dict[str, Any], *, description: str
) -> tuple[str, ...]:
    """Order parameters so every dependency is drawn before its dependent.

    Unknown names and cycles are refused here, at decoration time, rather
    than surfacing later as a confusing generation error.
    """
    remaining = dict(specifications)
    ordered: list[str] = []
    while remaining:
        ready = [
            name
            for name, specification in remaining.items()
            if not isinstance(specification, DependentDomain)
            or all(need in ordered for need in specification.dependencies)
        ]
        if not ready:
            blocked = ", ".join(sorted(remaining))
            raise TypeError(
                f"law '{description}' has dependent domains that cannot be "
                f"ordered: {blocked}; check for a cycle"
            )
        for name in ready:
            ordered.append(name)
            del remaining[name]
    return tuple(ordered)


def _bound(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite; a domain states real bounds")
    return float(value)


def number(low: int | float, high: int | float) -> Domain:
    """A finite float between ``low`` and ``high``, both included.

    Never generates NaN or infinity: a law that genuinely accepts those
    must say so explicitly with a Hypothesis strategy, because silently
    including them would make the visible contract lie in one direction,
    and silently excluding them from ``float`` lies in the other.
    """
    low_bound = _bound(low, name="low")
    high_bound = _bound(high, name="high")
    if low_bound > high_bound:
        raise ValueError(f"number({low!r}, {high!r}) is empty: low exceeds high")
    return Domain(
        description=f"number({low!r}, {high!r})",
        strategy=st.floats(
            min_value=low_bound,
            max_value=high_bound,
            allow_nan=False,
            allow_infinity=False,
        ),
    )


def integer(low: int, high: int) -> Domain:
    """A bounded integer, both bounds included.

    Evidenced by the kvfit probe (docs/kvfit-probe.md): topology laws
    needed bounded GPU and head counts, `number` is floats-only, and an
    unbounded ``int`` annotation was not an option because the planner
    scans divisors up to the generated count — a huge value would hang
    the law. Bounds must be real integers (bools are rejected) with
    ``low <= high``; an impossible domain fails at definition time.
    """
    for name, value in (("low", low), ("high", high)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
    if low > high:
        raise ValueError(f"integer({low!r}, {high!r}) is empty: low exceeds high")
    return Domain(
        description=f"integer({low!r}, {high!r})",
        strategy=st.integers(min_value=low, max_value=high),
    )


def one_of(values: Iterable[Any]) -> Domain:
    """A domain of finitely many accepted values, given explicitly.

    Some inputs are not a range but a computed set: the divisors of a
    GPU count, the supported dtypes, the states a machine may start in.
    The kvfit probes demonstrated this twice — a valid tensor-parallel
    size must divide the GPU count, and the laws that would have said so
    went unwritten because the domain could not be stated
    (docs/kvfit-probe.md, docs/package-boundary-probe.md).

    Most often it appears inside ``depends``, where the choices are
    computed from another drawn value::

        Gpus = tlaw.integer(1, 8)
        TensorParallel = tlaw.depends(
            lambda gpus: tlaw.one_of(
                [size for size in range(1, gpus + 1) if gpus % size == 0]
            )
        )

    Values are used exactly as given; the description lists them, or
    summarises with an honest count when there are many. An empty
    collection is refused at definition time — it could generate nothing,
    so every law using it would be vacuous.
    """
    materialized = tuple(values)
    if not materialized:
        raise ValueError(
            "one_of() needs at least one value; an empty domain would make "
            "every law using it vacuous"
        )
    if len(materialized) <= 6:
        shown = ", ".join(repr(value) for value in materialized)
    else:
        head = ", ".join(repr(value) for value in materialized[:4])
        shown = f"{head}, ... ({len(materialized)} values)"
    return Domain(
        description=f"one_of({shown})",
        strategy=st.sampled_from(materialized),
    )


def list_of(
    element: Domain | type,
    *,
    min_size: int = 0,
    max_size: int | None = None,
) -> Domain:
    """A list whose elements come from a domain or a plain type.

    Size bounds are part of the description because they are part of the
    contract: a law that only holds for non-empty lists must say so where
    the law is read.
    """
    if isinstance(element, Domain):
        element_description = element.description
        element_strategy = element.strategy
    elif isinstance(element, type):
        element_description = element.__name__
        element_strategy = st.from_type(element)
    else:
        raise TypeError(
            "list_of element must be a tlaw domain or a plain type; "
            f"got {type(element).__name__}"
        )
    if isinstance(min_size, bool) or not isinstance(min_size, int) or min_size < 0:
        raise ValueError("min_size must be a non-negative integer")
    if max_size is not None:
        if isinstance(max_size, bool) or not isinstance(max_size, int):
            raise ValueError("max_size must be an integer or None")
        if max_size < min_size:
            raise ValueError(
                f"list_of sizes are empty: max_size {max_size} below "
                f"min_size {min_size}"
            )

    described = [element_description]
    if min_size:
        described.append(f"min_size={min_size}")
    if max_size is not None:
        described.append(f"max_size={max_size}")
    return Domain(
        description=f"list_of({', '.join(described)})",
        strategy=st.lists(element_strategy, min_size=min_size, max_size=max_size),
    )
