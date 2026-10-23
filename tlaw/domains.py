"""Readable value domains that compile to Hypothesis strategies.

What
----
``number(low, high)`` declares a finite float between two bounds.
``integer(low, high)`` declares a bounded integer.
``list_of(element, ...)`` declares a list of a domain or plain type.
``recursive(base, extend, ...)`` declares finite recursively nested values.
``record(**fields)`` declares a composite: a dict, or a constructor
applied to the drawn fields.
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
  ``low <= high``; integer bounds must be exactly representable as floats
  so the generated domain cannot silently extend beyond the visible bounds.
  An impossible or unrepresentable domain is rejected at definition time,
  not discovered as a confusing generation error later.
- ``integer`` generates integers with both bounds included and rejects
  bools, non-integer bounds, and reversed bounds at definition time.
- ``list_of`` accepts a ``Domain`` or a plain type (``int``, ``str``);
  a plain type generates exactly what the bare annotation would, so the
  two spellings never disagree.
- ``recursive`` starts with a base domain and repeatedly applies a visible
  extension such as ``list_of``. Its minimum and maximum leaf budgets are
  explicit, and an extension that hides or ignores the recursive child is
  refused instead of being presented as recursive evidence.
- ``record`` accepts the same per field and nests through itself and
  ``list_of``, so a whole structured request is one declaration whose
  shape stays visible in ``--explain``.
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


@dataclass(frozen=True)
class ListShape:
    """List declarations retained independently of their sampling strategy."""

    element: Domain | type
    min_size: int
    max_size: int | None


@dataclass(frozen=True, eq=False)
class Domain:
    """A declared input domain: a description plus one Hypothesis strategy.

    The description is the whole point: it is what a reviewer sees in the
    law's signature and what ``--explain`` prints, so generation is never
    an invisible spec. The strategy is deliberately not part of ``repr``.
    """

    description: str
    strategy: SearchStrategy[Any] = field(repr=False)
    # The concrete Python types this domain generates, so an
    # ``Annotated[base, domain]`` whose base type contradicts them can be
    # refused instead of silently believed. Empty means "not declared",
    # and no check is made — tlaw never guesses a mismatch it cannot prove.
    generates: tuple[type, ...] = field(default=(), repr=False)
    # Internal provenance for recursive construction. Trusted combinators
    # propagate an opaque token when their strategy actually consumes the
    # recursive child; matching description text alone is not evidence.
    _references: frozenset[object] = field(
        default_factory=frozenset,
        init=False,
        repr=False,
        compare=False,
    )
    # Exact declaration entries, independent of the sampling strategy.
    finite_values: range | tuple[Any, ...] | None = field(
        default=None, init=False, repr=False
    )
    list_shape: ListShape | None = field(default=None, init=False, repr=False)
    record_fields: tuple[tuple[str, Domain | type | DependentDomain], ...] | None = (
        field(default=None, init=False, repr=False)
    )

    def __repr__(self) -> str:
        return self.description


def _with_references(domain: Domain, references: Iterable[object]) -> Domain:
    """Attach trusted composition provenance to one new frozen domain."""
    object.__setattr__(domain, "_references", frozenset(references))
    return domain


def _with_finite_metadata(
    domain: Domain,
    *,
    values: range | tuple[Any, ...] | None = None,
    list_shape: ListShape | None = None,
    record_fields: tuple[tuple[str, Domain | type | DependentDomain], ...]
    | None = None,
) -> Domain:
    """Retain exact declarations on a newly built trusted domain."""
    object.__setattr__(domain, "finite_values", values)
    object.__setattr__(domain, "list_shape", list_shape)
    object.__setattr__(domain, "record_fields", record_fields)
    return domain


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
    specifications: dict[str, Any], *, description: str, subject: str = "law"
) -> tuple[str, ...]:
    """Order names so every dependency is drawn before its dependent.

    Unknown names and cycles are refused here, at decoration time, rather
    than surfacing later as a confusing generation error. ``subject`` names
    the scope being ordered — a law's parameters or a record's fields — so
    the refusal cannot describe the wrong namespace.
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
                f"{subject} '{description}' has dependent domains that cannot "
                f"be ordered: {blocked}; check for a cycle"
            )
        for name in ready:
            ordered.append(name)
            del remaining[name]
    return tuple(ordered)


def _bound(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    try:
        converted = float(value)
    except OverflowError as error:
        raise ValueError(
            f"{name} must be exactly representable as a finite float"
        ) from error
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite; a domain states real bounds")
    if isinstance(value, int) and int(converted) != value:
        raise ValueError(
            f"{name} must be exactly representable as a finite float; "
            f"{value!r} would round to {converted!r}"
        )
    return converted


def number(low: int | float, high: int | float) -> Domain:
    """A finite float between ``low`` and ``high``, both included.

    Never generates NaN or infinity: a law that genuinely accepts those
    must say so explicitly with a Hypothesis strategy, because silently
    including them would make the visible contract lie in one direction,
    and silently excluding them from ``float`` lies in the other. An integer
    bound must be exactly representable as a finite float, because rounding it
    would make the displayed and generated bounds disagree.
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
        generates=(float,),
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
    return _with_finite_metadata(
        Domain(
            description=f"integer({low!r}, {high!r})",
            strategy=st.integers(min_value=low, max_value=high),
            generates=(int,),
        ),
        values=range(low, high + 1),
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
    return _with_finite_metadata(
        Domain(
            description=f"one_of({shown})",
            strategy=st.sampled_from(materialized),
            # The distinct types of the given values, so a base type that none
            # of them could satisfy is caught.
            generates=tuple(dict.fromkeys(type(value) for value in materialized)),
        ),
        values=materialized,
    )


def recursive(
    base: Domain | type,
    extend: Callable[[Domain], Domain],
    *,
    min_leaves: int | None = None,
    max_leaves: int = 100,
) -> Domain:
    """Generate finite recursively nested values with a visible leaf budget.

    ``base`` supplies leaves. ``extend`` receives a domain named ``self`` and
    must return one domain that adds a recursive layer::

        Trees = tlaw.recursive(
            tlaw.integer(0, 9),
            lambda child: tlaw.list_of(child, max_size=3),
            max_leaves=20,
        )

    The generated strategy has Hypothesis's recursive semantics: it draws a
    base value or a value reachable by applying ``extend``. ``max_leaves``
    bounds how many base values one example may contain; ``min_leaves`` can
    require a non-trivial minimum. Both are leaf counts, not a claimed depth
    limit.

    The extension's description must contain the child domain. This keeps
    ``--explain`` honest: a builder that ignores its argument, or hides it in
    an opaque strategy, is not accepted as a visible recursive contract.
    """
    if isinstance(base, Domain):
        base_description = base.description
        base_strategy = base.strategy
        base_generates = base.generates
        base_references = base._references
    elif isinstance(base, type):
        base_description = base.__name__
        base_strategy = st.from_type(base)
        base_generates = (base,)
        base_references = frozenset()
    else:
        raise TypeError(
            "recursive base must be a tlaw domain or a plain type; "
            f"got {type(base).__name__}"
        )
    if not callable(extend):
        raise TypeError(
            "recursive extend must be a function that returns a tlaw domain"
        )

    if isinstance(max_leaves, bool) or not isinstance(max_leaves, int):
        raise ValueError("max_leaves must be a positive integer")
    if max_leaves <= 0:
        raise ValueError("max_leaves must be a positive integer")
    if min_leaves is not None:
        if isinstance(min_leaves, bool) or not isinstance(min_leaves, int):
            raise ValueError("min_leaves must be a positive integer or None")
        if min_leaves <= 0:
            raise ValueError("min_leaves must be a positive integer or None")
        if min_leaves > max_leaves:
            raise ValueError("min_leaves cannot exceed max_leaves")

    marker = "<tlaw-recursive-self>"
    reference = object()
    placeholder = _with_references(
        Domain(marker, st.nothing()),
        (reference,),
    )
    try:
        parameters = tuple(inspect.signature(extend).parameters.values())
    except (TypeError, ValueError) as error:
        raise TypeError(
            "recursive extend must accept exactly one child domain"
        ) from error
    if len(parameters) != 1 or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise TypeError("recursive extend must accept exactly one child domain")

    preview = extend(placeholder)
    if not isinstance(preview, Domain):
        raise TypeError(
            f"recursive extend must return a tlaw domain; got {type(preview).__name__}"
        )
    if marker not in preview.description:
        raise ValueError(
            "recursive extend must use the child domain in its visible shape"
        )
    if reference not in preview._references:
        raise ValueError(
            "recursive extend must compose the child through a tlaw domain "
            "builder; description text alone does not prove recursion"
        )
    extension_description = preview.description.replace(marker, "self")

    def extend_strategy(children: SearchStrategy[Any]) -> SearchStrategy[Any]:
        extended = extend(
            _with_references(
                Domain(marker, children),
                (reference,),
            )
        )
        if not isinstance(extended, Domain):
            raise TypeError(
                "recursive extend must always return a tlaw domain; "
                f"got {type(extended).__name__}"
            )
        if extended.description != preview.description:
            raise ValueError("recursive extend must declare one stable visible shape")
        if reference not in extended._references:
            raise ValueError("recursive extend must always compose the recursive child")
        return extended.strategy

    described_budget = []
    if min_leaves is not None:
        described_budget.append(f"min_leaves={min_leaves}")
    described_budget.append(f"max_leaves={max_leaves}")
    description = (
        f"recursive({base_description}, extend={extension_description}, "
        f"{', '.join(described_budget)})"
    )
    generates = (
        tuple(dict.fromkeys((*base_generates, *preview.generates)))
        if base_generates and preview.generates
        else ()
    )
    return _with_references(
        Domain(
            description=description,
            strategy=st.recursive(
                base_strategy,
                extend_strategy,
                min_leaves=min_leaves,
                max_leaves=max_leaves,
            ),
            generates=generates,
        ),
        base_references,
    )


def record(constructor: Callable[..., Any] | None = None, /, **fields: Any) -> Domain:
    """A value with fixed named fields, each drawn from its own domain.

    Valid input is often a structure, not a scalar. The anef probe's
    flagship parity law needs a whole verification request — a nested
    composite of documents, sources, and field selections — and with only
    scalar and list domains that law could not be written generatively at
    all; it degraded to a hand-curated ``one_of`` of seed values (issue #1,
    gap G1).

    Without a constructor, a dictionary is generated, which is what a JSON
    payload wants::

        Source = tlaw.record(attachment_id=Identifiers)
        Payload = tlaw.record(
            schema_version=tlaw.one_of(["verification.request.v2"]),
            documents=tlaw.list_of(Document, min_size=1, max_size=2),
        )

    With one, it is applied to the drawn fields by keyword, which is what a
    dataclass wants::

        Document = tlaw.record(
            DocumentRequest,
            id=DocumentIds,
            type=DocumentTypes,
            source=Sources,
            fields=Selections,
        )

    Records nest through each other and through ``list_of``, so a whole
    request is one readable declaration and ``--explain`` prints the shape
    that was actually generated.

    Each field is a tlaw domain or a plain type, exactly as ``list_of``
    accepts them — a constant is stated as ``one_of([value])``, so a value
    is never mistaken for a type or the other way round.

    A field may also be a ``depends(...)`` domain, and inside a record it
    names **sibling fields of that same record**::

        Selections = tlaw.one_of([("person_name",), ("birth_date",)])
        Document = tlaw.record(
            fields=Selections,
            check=tlaw.depends(lambda fields: tlaw.one_of(check_on(fields))),
        )

    Siblings are the only namespace a record's ``depends`` can reach. It
    cannot name a law parameter, and a name that is not a sibling is
    refused when the record is defined, saying so. ``depends`` at law level
    already names law parameters, and resolving one word against whichever
    namespace happened to contain the name would be a guess about which
    contract the author meant. Fields are drawn in dependency order and the
    relation holds by construction — nothing is filtered, so no example is
    discarded and no law using the record can quietly become vacuous.
    """
    if constructor is not None and not callable(constructor):
        raise TypeError(
            "record()'s first argument is the constructor applied to the drawn "
            f"fields; got {type(constructor).__name__}, which is not callable"
        )
    if not fields:
        raise ValueError(
            "record() needs at least one named field; a record with no fields "
            "generates one constant value and states nothing"
        )

    described: list[str] = []
    references: set[object] = set()
    specifications: dict[str, SearchStrategy[Any] | DependentDomain] = {}
    for name, specification in fields.items():
        if isinstance(specification, DependentDomain):
            described.append(f"{name}={specification.description}")
            specifications[name] = specification
        elif isinstance(specification, Domain):
            described.append(f"{name}={specification.description}")
            specifications[name] = specification.strategy
            references.update(specification._references)
        elif isinstance(specification, type):
            described.append(f"{name}={specification.__name__}")
            specifications[name] = st.from_type(specification)
        else:
            raise TypeError(
                f"record() field '{name}' must be a tlaw domain or a plain type; "
                f"got {type(specification).__name__}. State a constant field as "
                "one_of([value])"
            )

    label = "record"
    if constructor is not None:
        label = f"record({getattr(constructor, '__name__', type(constructor).__name__)}"
    description = (
        f"record({', '.join(described)})"
        if constructor is None
        else f"{label}, {', '.join(described)})"
    )

    # A record's depends() names siblings, so an unknown name is refused
    # here rather than resolved against the law's parameters — one word
    # must not silently mean two different namespaces.
    unknown = {
        need: name
        for name, specification in specifications.items()
        if isinstance(specification, DependentDomain)
        for need in specification.dependencies
        if need not in specifications
    }
    if unknown:
        missing = ", ".join(sorted(unknown))
        siblings = ", ".join(sorted(specifications))
        raise TypeError(
            f"record() field '{unknown[sorted(unknown)[0]]}' depends on "
            f"{missing}, which is not a field of this record. A record's "
            f"depends() names sibling fields only ({siblings}); it cannot "
            "reach a law parameter, because one name must not mean two "
            "different scopes"
        )

    drawn_in_order = generation_order(
        specifications, description=description, subject="record"
    )

    if any(isinstance(value, DependentDomain) for value in specifications.values()):

        @st.composite
        def draw_fields(draw: Any) -> dict[str, Any]:
            values: dict[str, Any] = {}
            for name in drawn_in_order:
                specification = specifications[name]
                if isinstance(specification, DependentDomain):
                    values[name] = draw(specification.resolve(values).strategy)
                else:
                    values[name] = draw(specification)
            return values

        fields_strategy = draw_fields()
    else:
        fields_strategy = st.fixed_dictionaries(
            {name: value for name, value in specifications.items()}
        )

    if constructor is None:
        return _with_finite_metadata(
            _with_references(
                Domain(
                    description=description,
                    strategy=fields_strategy,
                    generates=(dict,),
                ),
                references,
            ),
            record_fields=tuple(fields.items()),
        )
    return _with_references(
        Domain(
            description=description,
            strategy=fields_strategy.map(lambda drawn: constructor(**drawn)),
            # A class states what it builds; any other callable does not, and
            # tlaw never claims a generated type it cannot prove.
            generates=(constructor,) if isinstance(constructor, type) else (),
        ),
        references,
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
        references = element._references
    elif isinstance(element, type):
        element_description = element.__name__
        element_strategy = st.from_type(element)
        references = frozenset()
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
    return _with_finite_metadata(
        _with_references(
            Domain(
                description=f"list_of({', '.join(described)})",
                strategy=st.lists(
                    element_strategy,
                    min_size=min_size,
                    max_size=max_size,
                ),
                generates=(list,),
            ),
            references,
        ),
        list_shape=ListShape(element, min_size, max_size),
    )
