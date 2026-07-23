"""The small, code-first interface for writing executable laws."""

from __future__ import annotations

import copy
import inspect
import math
import os
import types
from typing import Union
from dataclasses import dataclass, field, replace
from functools import wraps
from typing import (
    Annotated,
    Any,
    Callable,
    Generic,
    TypeAliasType,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from hypothesis import given, seed as hypothesis_seed, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from .audit import TARGET_ENV
from .category import Arrow, arrow, identity
from .comparison import Comparison
from .domains import DependentDomain, Domain, generation_order
from .refusal import Refusal
from .replay import (
    MODE_ENV,
    REPLAY_ENV,
    SEED_ENV,
    capture_failure,
    decoded_arguments,
    exception_evidence,
    prepare_failure_inputs,
    replay_failures,
)
from .target import load_target


Value = TypeVar("Value")
Observation = TypeVar("Observation")
INFERRED_MAX_EXAMPLES = 100


def _unchanged(value: Value) -> Value:
    return value


def _call_text(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    arguments = [*(repr(argument) for argument in args)]
    arguments.extend(f"{key}={value!r}" for key, value in kwargs.items())
    return f"{method}({', '.join(arguments)})"


@dataclass(frozen=True)
class System(Generic[Value, Observation]):
    """Construct fresh values and define what should be observed about them."""

    implementation: Callable[..., Value]
    observe: Callable[[Value], Observation]
    name: str
    start: Callable[..., Value] | None = field(default=None, repr=False)

    def __call__(self, *args: Any, **kwargs: Any) -> Path[Value, Observation]:
        return Path(
            system=self,
            args=args,
            kwargs=tuple(kwargs.items()),
            transformation=identity(),
        )

    def build(
        self, args: tuple[Any, ...], kwargs: tuple[tuple[str, Any], ...]
    ) -> Value:
        implementation = (
            load_target() if os.environ.get(TARGET_ENV) else self.implementation
        )
        if self.start is None:
            return implementation(*args, **dict(kwargs))
        return self.start(implementation, *args, **dict(kwargs))


@dataclass(frozen=True)
class Path(Generic[Value, Observation]):
    """A fresh start followed by an immutable sequence of steps."""

    system: System[Value, Observation]
    args: tuple[Any, ...]
    kwargs: tuple[tuple[str, Any], ...]
    transformation: Arrow[Any, Any]

    def then(
        self,
        function: Callable[[Any], Any],
        *,
        name: str | None = None,
    ) -> Path[Any, Any]:
        """Append a value-returning transformation."""
        return replace(
            self,
            transformation=self.transformation >> arrow(function, name=name),
        )

    def do(
        self,
        action: Callable[[Value], object],
        *,
        name: str | None = None,
    ) -> Path[Value, Observation]:
        """Append an in-place action while retaining the current value."""

        def perform(value: Value) -> Value:
            action(value)
            return value

        return replace(
            self,
            transformation=self.transformation >> arrow(perform, name=name),
        )

    def call(self, method: str, *args: Any, **kwargs: Any) -> Path[Value, Observation]:
        """Append a dynamically dispatched, in-place method call."""
        if not method:
            raise ValueError("a method name cannot be empty")

        def perform(value: Value) -> Value:
            getattr(value, method)(*args, **kwargs)
            return value

        return replace(
            self,
            transformation=self.transformation
            >> arrow(perform, name=_call_text(method, args, kwargs)),
        )

    def same_as(self, other: Path[Value, Observation]) -> Equation[Observation]:
        """State that two paths should have the same observation."""
        if self.system is not other.system:
            raise ValueError("parallel paths must belong to the same tlaw system")
        return Equation(left=self, right=other)

    def result(self) -> Observation:
        """Execute this path from a fresh start and observe its result."""
        start = self.system.build(self.args, self.kwargs)
        result = self.transformation(start)
        return self.system.observe(result)

    def render(self) -> str:
        arguments = [*(repr(argument) for argument in self.args)]
        arguments.extend(f"{key}={value!r}" for key, value in self.kwargs)
        start = f"{self.system.name}({', '.join(arguments)})"
        if not self.transformation.boxes:
            return start
        calls = ".".join(box.name for box in self.transformation.boxes)
        return f"{start}.{calls}"


@dataclass(frozen=True)
class Equation(Generic[Observation]):
    """Two executable paths asserted to have equal observations."""

    left: Path[Any, Observation]
    right: Path[Any, Observation]

    def check(self, description: str = "law") -> None:
        left_result = self.left.result()
        right_result = self.right.result()
        if left_result != right_result:
            raise AssertionError(
                f"law failed: {description}\n"
                f"  {self.left.render()} -> {left_result!r}\n"
                f"  {self.right.render()} -> {right_result!r}"
            )


LawResult = Equation[Any] | Comparison | Refusal | bool | None
LawFunction = Callable[..., LawResult]


def _copied_value_is_unchanged(before: Any, after: Any) -> bool | None:
    """Compare copied inputs conservatively without guessing array truth values."""
    if before is after:
        return True
    if type(before) is not type(after):
        return False
    if isinstance(before, float) and math.isnan(before) and math.isnan(after):
        return True
    if isinstance(before, dict):
        if before.keys() != after.keys():
            return False
        comparisons = (
            _copied_value_is_unchanged(before[key], after[key]) for key in before
        )
    elif isinstance(before, (list, tuple)):
        if len(before) != len(after):
            return False
        comparisons = (
            _copied_value_is_unchanged(left, right)
            for left, right in zip(before, after)
        )
    else:
        try:
            comparison = before == after
        except Exception:
            return None
        return comparison if isinstance(comparison, bool) else None

    saw_unknown = False
    for comparison in comparisons:
        if comparison is False:
            return False
        if comparison is None:
            saw_unknown = True
    return None if saw_unknown else True


def _snapshot_generated_inputs(
    function: LawFunction,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    description: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = dict(inspect.signature(function).bind(*args, **kwargs).arguments)
    snapshots: dict[str, Any] = {}
    for name, value in current.items():
        try:
            snapshots[name] = copy.deepcopy(value)
        except Exception as error:
            raise TypeError(
                f"law '{description}' received input '{name}' of type "
                f"{type(value).__name__}, which tlaw could not copy to check for "
                "mutation; use immutable inputs or the explicit stateful path API"
            ) from error
    return current, snapshots


def _ensure_generated_inputs_unchanged(
    current: dict[str, Any],
    snapshots: dict[str, Any],
    *,
    description: str,
) -> None:
    for name, before in snapshots.items():
        after = current[name]
        unchanged = _copied_value_is_unchanged(before, after)
        if unchanged is False:
            raise AssertionError(
                f"law '{description}' passed after mutating generated input "
                f"'{name}'; bare @tlaw.law is for pure behavior, so compare "
                "independent values or use the explicit stateful path API"
            )
        if unchanged is None:
            raise TypeError(
                f"law '{description}' passed, but tlaw could not verify that "
                f"input '{name}' of type {type(after).__name__} remained unchanged; "
                "use immutable inputs or the explicit stateful path API"
            )


def _render_annotation(annotation: Any) -> str:
    """Render an annotation for --explain, expanding domains and aliases.

    A PEP 695 alias such as ``list[Reward]`` where
    ``Reward = Annotated[float, tlaw.number(0, 1)]`` is expanded to
    ``list[number(0, 1)]``, so the generated domain is visible in the
    explanation line rather than hidden behind the alias name. Only
    annotations that mention a domain take this path; everything else
    keeps Python's ordinary formatting.
    """
    annotation = _unwrap_alias(annotation)
    if isinstance(annotation, Domain | DependentDomain):
        return annotation.description
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *metadata = get_args(annotation)
        domains = [value for value in metadata if isinstance(value, Domain)]
        if len(domains) == 1:
            return domains[0].description
        return _render_annotation(base)
    arguments = get_args(annotation)
    if origin is not None and arguments:
        origin_name = getattr(origin, "__name__", None) or inspect.formatannotation(
            origin
        )
        rendered = ", ".join(_render_annotation(argument) for argument in arguments)
        return f"{origin_name}[{rendered}]"
    return inspect.formatannotation(annotation)


def _annotation_text(function: LawFunction) -> tuple[str, ...]:
    try:
        hints = get_type_hints(function, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    try:
        # typing rejects tlaw domains because they are values, not types;
        # inspect resolves string annotations to the actual objects so
        # --explain can print a domain's own description.
        resolved = inspect.get_annotations(function, eval_str=True)
    except (NameError, TypeError):
        resolved = {}
    texts = []
    for parameter in inspect.signature(function).parameters.values():
        annotation = hints.get(
            parameter.name, resolved.get(parameter.name, parameter.annotation)
        )
        if _mentions_domain(annotation):
            rendered = _render_annotation(annotation)
        else:
            rendered = inspect.formatannotation(annotation)
        texts.append(f"{parameter.name}: {rendered}")
    return tuple(texts)


def _mentions_domain(annotation: Any) -> bool:
    annotation = _unwrap_alias(annotation)
    if isinstance(annotation, Domain | DependentDomain):
        return True
    return any(_mentions_domain(value) for value in get_args(annotation))


def _annotation_base_classes(base: Any) -> tuple[type, ...] | None:
    """Reduce an annotation to the concrete classes it permits, or None.

    None means "cannot prove anything" — ``Any``, a bare ``object``, a
    type variable, or a member of a union that itself accepts anything.
    In that case tlaw makes no claim, because it never refuses a mismatch
    it cannot prove.
    """
    base = _unwrap_alias(base)
    if base is Any or base is object:
        return None
    origin = get_origin(base)
    if origin is None:
        return (base,) if isinstance(base, type) else None
    if origin is Union or origin is types.UnionType:
        classes: list[type] = []
        for argument in get_args(base):
            reduced = _annotation_base_classes(argument)
            if reduced is None:
                return None  # a branch accepts anything: prove nothing
            classes.extend(reduced)
        return tuple(classes)
    if isinstance(origin, type):
        return (origin,)  # list[int] -> list, dict[str, int] -> dict
    return None


def _verify_base_matches_domain(
    base: Any, domain: Domain, *, description: str, parameter: str
) -> None:
    """Refuse ``Annotated[base, domain]`` when the two provably disagree.

    The base type is what a reader and a type checker believe flows
    through; the domain is what tlaw actually generates. When the base
    can only be a type that the domain never produces —
    ``Annotated[str, tlaw.number(0, 1)]`` — the visible contract lies, so
    it is refused at decoration time. When either side is unprovable, tlaw
    stays silent rather than guess.
    """
    if not domain.generates:
        return
    allowed = _annotation_base_classes(base)
    if allowed is None:
        return
    for generated in domain.generates:
        try:
            compatible = any(issubclass(generated, permitted) for permitted in allowed)
        except TypeError:
            return
        if not compatible:
            break
    else:
        return
    generated_names = "/".join(sorted(t.__name__ for t in domain.generates))
    base_names = "/".join(sorted(t.__name__ for t in allowed))
    raise TypeError(
        f"law '{description}' parameter '{parameter}': the Annotated base type "
        f"({base_names}) cannot hold what {domain.description} generates "
        f"({generated_names}); the visible contract would claim a type the "
        "domain never produces"
    )


def _domain_strategy(
    annotation: Any, *, description: str, parameter: str
) -> SearchStrategy[Any] | DependentDomain:
    """Compile one annotation that mentions a tlaw domain into a strategy.

    Supported spellings, chosen to cover both ways a real laws file
    writes the retrain probe's candidate:

    - a bare domain value: ``rewards: Rewards``;
    - ``Annotated[float, tlaw.number(0, 1)]`` — the type checker reads
      the base type, tlaw enforces the domain; and
    - ``list[...]`` of either, including through PEP 695 ``type``
      aliases, so ``rewards: list[Reward]`` works.

    A domain in any other position is refused with the position named,
    because generating from the base type instead would silently erase
    the declared constraint — the exact dishonesty the Annotated
    rejection exists to prevent.
    """
    annotation = _unwrap_alias(annotation)
    if isinstance(annotation, Domain):
        return annotation.strategy
    if isinstance(annotation, DependentDomain):
        # Returned whole: its strategy does not exist until the values it
        # depends on have been drawn, which happens in _ordered_inputs.
        return annotation
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        domains = [value for value in metadata if isinstance(value, Domain)]
        if len(domains) == 1 and len(metadata) == 1:
            _verify_base_matches_domain(
                base, domains[0], description=description, parameter=parameter
            )
            return domains[0].strategy
        raise TypeError(
            f"law '{description}' parameter '{parameter}' mixes a tlaw domain "
            "with other Annotated metadata; tlaw enforces exactly one domain "
            "per Annotated value"
        )
    if get_origin(annotation) is list:
        arguments = get_args(annotation)
        if len(arguments) == 1:
            return st.lists(
                _domain_strategy(
                    arguments[0], description=description, parameter=parameter
                )
            )
    raise TypeError(
        f"law '{description}' parameter '{parameter}' uses a tlaw domain in a "
        "position tlaw cannot enforce yet; supported: a bare domain, "
        "Annotated[type, domain], and list[...] of those"
    )


def _ordered_inputs(
    specifications: dict[str, SearchStrategy[Any] | DependentDomain],
    *,
    description: str,
) -> SearchStrategy[dict[str, Any]]:
    """Draw every parameter in dependency order as one composite value.

    Hypothesis draws ``given`` arguments independently, so a dependent
    domain cannot be expressed as one strategy per parameter. tlaw
    instead draws them all inside one composite, in an order where each
    dependency is already available, and the law is then called with the
    resulting values under their own names.

    Dependencies are satisfied by *construction*, never by filtering, so
    no example is discarded and no law can quietly become vacuous.
    """
    # Unknown names are checked before ordering: an unresolvable name
    # would otherwise surface as a confusing "cycle" report.
    missing = {
        need
        for specification in specifications.values()
        if isinstance(specification, DependentDomain)
        for need in specification.dependencies
        if need not in specifications
    }
    if missing:
        names = ", ".join(sorted(missing))
        raise TypeError(
            f"law '{description}' has a dependent domain naming unknown "
            f"parameters: {names}"
        )
    order = generation_order(specifications, description=description)

    @st.composite
    def draw_all(draw: Any) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name in order:
            specification = specifications[name]
            if isinstance(specification, DependentDomain):
                values[name] = draw(specification.resolve(values).strategy)
            else:
                values[name] = draw(specification)
        return values

    return draw_all()


def _domain_strategies(
    function: LawFunction, *, description: str
) -> dict[str, SearchStrategy[Any] | DependentDomain] | None:
    """Resolve annotations to strategies when any of them uses a tlaw domain.

    Domains are values, so Hypothesis's own type-based inference cannot
    see them; tlaw resolves the annotations itself in that case. String
    annotations (``from __future__ import annotations``) are evaluated
    against the law module's globals, which is why domains used as
    annotations must be module-level names. When no domain is present,
    this returns None and the ordinary inference path is untouched.
    """
    try:
        annotations = inspect.get_annotations(function, eval_str=True)
    except (NameError, TypeError):
        return None
    annotations.pop("return", None)
    if not any(_mentions_domain(value) for value in annotations.values()):
        return None

    strategies: dict[str, SearchStrategy[Any] | DependentDomain] = {}
    for parameter in inspect.signature(function).parameters.values():
        annotation = annotations.get(parameter.name, inspect.Parameter.empty)
        if annotation is inspect.Parameter.empty:
            # A half-declared signature cannot be enforced; refusing is
            # the same rule as rejecting Annotated metadata tlaw cannot
            # honor, applied to tlaw's own feature.
            raise TypeError(
                f"law '{description}' mixes tlaw domains with the unannotated "
                f"parameter '{parameter.name}'; every generated input needs a "
                "domain or a type"
            )
        if _mentions_domain(annotation):
            strategies[parameter.name] = _domain_strategy(
                annotation, description=description, parameter=parameter.name
            )
        else:
            strategies[parameter.name] = st.from_type(annotation)
    return strategies


def _unwrap_alias(annotation: Any) -> Any:
    """Follow PEP 695 ``type`` aliases to the annotation they name."""
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    return annotation


def _contains_annotated_metadata(annotation: Any) -> bool:
    """True when metadata is present that tlaw cannot enforce.

    ``Annotated`` carrying exactly one tlaw domain is enforceable — the
    domain generates the inputs — so it is not rejected. Anything else
    inside ``Annotated`` would be silently erased during inference, and
    tlaw refuses to let the visible contract claim more than generation
    delivers.
    """
    annotation = _unwrap_alias(annotation)
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        if len(metadata) == 1 and isinstance(metadata[0], Domain):
            return _contains_annotated_metadata(base)
        return True
    return any(_contains_annotated_metadata(value) for value in get_args(annotation))


def _reject_unsupported_annotation_metadata(function: LawFunction) -> None:
    try:
        hints = get_type_hints(function, include_extras=True)
    except (NameError, TypeError):
        return
    unsupported = [
        name
        for name, annotation in hints.items()
        if _contains_annotated_metadata(annotation)
    ]
    if unsupported:
        names = ", ".join(unsupported)
        raise TypeError(
            f"@tlaw.law does not yet enforce Annotated metadata for: {names}; "
            "tlaw refuses to ignore input-domain constraints"
        )


def system(
    implementation: Callable[..., Value],
    *,
    observe: Callable[[Value], Observation] | None = None,
    name: str | None = None,
    start: Callable[..., Value] | None = None,
) -> System[Value, Observation | Value]:
    """Define how a law constructs and observes fresh application values."""
    system_name = name or getattr(
        implementation, "__name__", type(implementation).__name__
    )
    if not system_name:
        raise ValueError("a system name cannot be empty")
    return System(
        implementation=implementation,
        observe=observe or _unchanged,
        name=system_name,
        start=start,
    )


def _spread(
    check: Callable[..., None], function: LawFunction
) -> Callable[[dict[str, Any]], None]:
    """Call the law with one composite draw spread back into its own names.

    Dependent domains are drawn together, but the law keeps its natural
    signature, so the values are handed back under the parameter names
    the author wrote. Hypothesis inspects this wrapper's signature, so it
    deliberately does not use ``functools.wraps`` — only the name and doc
    are copied, for pytest's report.
    """

    def run(_tlaw_generated: dict[str, Any]) -> None:
        check(**_tlaw_generated)

    run.__name__ = function.__name__
    run.__doc__ = function.__doc__
    return run


def _decorate_law(
    function: LawFunction,
    *,
    description: str,
    strategies: dict[str, SearchStrategy[Any] | Domain | DependentDomain] | None,
) -> Callable[[], None]:
    inferred = strategies is None
    search = os.environ.get(MODE_ENV) == "search"
    if search:
        generation = (
            "search-seeded-inferred" if inferred else "search-seeded-explicit-settings"
        )
    else:
        generation = "inferred-deterministic" if inferred else "explicit-settings"

    @wraps(function)
    def check(*args: Any, **kwargs: Any) -> None:
        current = dict(inspect.signature(function).bind(*args, **kwargs).arguments)
        prepared_inputs = prepare_failure_inputs(current)
        snapshots: dict[str, Any] = {}
        comparison: Comparison | None = None
        try:
            if inferred:
                current, snapshots = _snapshot_generated_inputs(
                    function,
                    args,
                    kwargs,
                    description=description,
                )

            result = function(*args, **kwargs)
            if isinstance(result, Equation):
                result.check(description)
            elif isinstance(result, Comparison):
                # tlaw owns this comparison, so the failure can show both
                # sides and the first difference instead of a bare False.
                comparison = result
                result.check(description)
            elif isinstance(result, Refusal):
                # tlaw performed this call, so the failure can say whether
                # the wrong exception was raised or a value came back.
                result.check(description)
            elif result is False:
                raise AssertionError(f"law failed: {description}")
            elif result is not True and result is not None:
                result_type = f"{type(result).__module__}.{type(result).__qualname__}"
                raise TypeError(
                    f"law '{description}' returned {result_type}, not one Python "
                    "boolean; some libraries return one value per element, so tlaw "
                    "cannot unambiguously decide whether the law passed"
                )

            if inferred:
                _ensure_generated_inputs_unchanged(
                    current,
                    snapshots,
                    description=description,
                )
        except Exception as error:
            capture_failure(
                function,
                description=description,
                generation=generation,
                arguments=current,
                prepared_inputs=prepared_inputs,
                error=error,
                comparison=comparison,
            )
            raise

    if os.environ.get(REPLAY_ENV):
        failures = replay_failures(function)
        if not failures:
            # Replay means exactly the retained failures, not every law or
            # ordinary pytest test that happens to share the file.
            def ignored() -> None:
                return None

            ignored.__name__ = function.__name__
            ignored.__test__ = False
            return ignored

        def replay_test() -> None:
            for index, failure in enumerate(failures, start=1):
                label = (
                    "retained counterexample"
                    if len(failures) == 1
                    else f"retained counterexample {index}"
                )
                positional, keywords = decoded_arguments(function, failure)
                expected = failure["exception"]
                try:
                    check(*positional, **keywords)
                except Exception as error:
                    actual = exception_evidence(error)
                    if actual == expected:
                        continue
                    raise AssertionError(
                        f"{label} changed its failure:\n"
                        f"  expected {expected['type']}: {expected['message']}\n"
                        f"  observed {actual['type']}: {actual['message']}"
                    ) from error
                raise AssertionError(f"{label} no longer fails")

        replay_test.__name__ = function.__name__
        replay_test.__doc__ = function.__doc__
        replay_test.__test__ = True
        replay_test.__tlaw_description__ = description
        replay_test.__tlaw_replay_identity__ = failures[0]["identity"]
        return replay_test

    if inferred:
        resolved = _domain_strategies(function, description=description)
        if resolved is None:
            hypothesis_test = given(...)(check)
        elif any(isinstance(value, DependentDomain) for value in resolved.values()):
            hypothesis_test = given(
                _tlaw_generated=_ordered_inputs(resolved, description=description)
            )(_spread(check, function))
        else:
            hypothesis_test = given(**resolved)(check)
        hypothesis_test = settings(
            database=None,
            derandomize=not search,
            max_examples=INFERRED_MAX_EXAMPLES,
        )(hypothesis_test)
        inputs = _annotation_text(function)
    else:
        # The explicit form deliberately keeps ordinary Hypothesis
        # semantics — no forced settings, because authors stack their own
        # ``@settings`` on top and Hypothesis rejects a second
        # application. That difference in posture must be visible, not
        # silent (kvfit probe, bottleneck 2), so the inputs are recorded
        # here — domain descriptions before compilation, strategy reprs
        # otherwise — and ``--explain`` states the posture.
        inputs = tuple(f"{name}: {value!r}" for name, value in strategies.items())
        hypothesis_test = given(
            **{
                name: value.strategy if isinstance(value, Domain) else value
                for name, value in strategies.items()
            }
        )(check)
    if search:
        seed = os.environ.get(SEED_ENV)
        if seed is None:
            raise RuntimeError("tlaw search has no seed")
        hypothesis_test = hypothesis_seed(int(seed))(hypothesis_test)
    hypothesis_test.__test__ = True
    hypothesis_test.__tlaw_description__ = description
    hypothesis_test.__tlaw_inputs__ = inputs
    hypothesis_test.__tlaw_max_examples__ = INFERRED_MAX_EXAMPLES if inferred else None
    hypothesis_test.__tlaw_deterministic__ = inferred and not search
    return hypothesis_test


@overload
def law(function: LawFunction, /) -> Callable[[], None]: ...


@overload
def law(
    description: str,
    /,
    **strategies: SearchStrategy[Any] | Domain,
) -> Callable[[LawFunction], Callable[[], None]]: ...


def law(
    function_or_description: LawFunction | str,
    /,
    **strategies: SearchStrategy[Any] | Domain,
) -> Callable[[], None] | Callable[[LawFunction], Callable[[], None]]:
    """Turn an executable law into a Hypothesis test collected by pytest.

    Used as ``@law``, generated inputs come from parameter annotations —
    plain types through Hypothesis's inference, and tlaw domains such as
    ``number(0, 1)``, ``list_of(...)``, or ``depends(...)`` through their
    declared strategies — and the function name becomes its description. The
    explicit ``@law(description, value=strategy)`` form remains available
    when a law needs Hypothesis syntax directly; it accepts domains too.
    """
    if callable(function_or_description):
        if strategies:
            raise TypeError("@law cannot combine inferred and explicit strategies")
        _reject_unsupported_annotation_metadata(function_or_description)
        description = function_or_description.__name__.replace("_", " ")
        return _decorate_law(
            function_or_description,
            description=description,
            strategies=None,
        )

    description = function_or_description
    if not description.strip():
        raise ValueError("a law description cannot be empty")

    def decorate(function: LawFunction) -> Callable[[], None]:
        return _decorate_law(
            function,
            description=description,
            strategies=dict(strategies),
        )

    return decorate
