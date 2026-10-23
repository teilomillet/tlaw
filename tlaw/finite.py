"""Exact, bounded enumeration of declared inputs; no strategy introspection."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product
from typing import Any, Literal, get_args, get_origin

from .domains import Domain


DEFAULT_MAX_CASES = 1_000
MAX_CASES_ENV = "TLAW_MAX_CASES"


@dataclass(frozen=True)
class ListValues:
    elements: FiniteValues
    min_size: int
    max_size: int


@dataclass(frozen=True)
class RecordValues:
    fields: tuple[tuple[str, FiniteValues], ...]


type FiniteValues = range | tuple[Any, ...] | ListValues | RecordValues


def _check_value(value: Any, containers: set[int]) -> None:
    """Only built-in replay values preserve their types across retained evidence."""
    kind = type(value)
    if kind in (type(None), bool, int, float, str, bytes):
        return
    if kind in (list, tuple, set, frozenset, dict):
        identity = id(value)
        if identity in containers:
            raise TypeError(
                "exhaustive choices cannot contain shared or cyclic container "
                "references; replay retains values, not reference identities"
            )
        containers.add(identity)
    if kind in (list, tuple, set, frozenset):
        for item in value:
            _check_value(item, containers)
        return
    if kind is dict:
        for key, item in value.items():
            _check_value(key, containers)
            _check_value(item, containers)
        return
    raise TypeError(
        f"exhaustive choices need replay-safe built-in values; got {kind.__name__}"
    )


def _values(specification: Any) -> FiniteValues:
    if isinstance(specification, Domain):
        if specification.record_fields is not None:
            return RecordValues(
                tuple(
                    (name, _values(value))
                    for name, value in specification.record_fields
                )
            )
        if specification.list_shape is not None:
            shape = specification.list_shape
            if shape.max_size is None:
                raise TypeError("exhaustive list_of requires an explicit max_size")
            if shape.max_size > sys.maxsize:
                raise TypeError("exhaustive list_of max_size exceeds sys.maxsize")
            return ListValues(_values(shape.element), shape.min_size, shape.max_size)
        if specification.finite_values is not None:
            return specification.finite_values
    if specification is bool:
        return (False, True)
    if specification is None or specification is type(None):
        return (None,)
    if get_origin(specification) is Literal:
        return get_args(specification)
    raise TypeError(
        f"exhaustive checking does not support {specification!r}; use bool, None, "
        "Literal, integer(...), one_of(...), bounded list_of(...), or "
        "dictionary-form record(...) with finite independent fields"
    )


def _count(values: FiniteValues, limit: int) -> int:
    """Count exactly up to the budget, then saturate without giant powers."""
    if isinstance(values, RecordValues):
        total = 1
        for _, value in values.fields:
            total *= _count(value, limit)
            if total > limit:
                return limit + 1
        return total
    if not isinstance(values, ListValues):
        count = values.stop - values.start if isinstance(values, range) else len(values)
        if count == 0:
            raise TypeError("exhaustive checking refuses an empty domain")
        return min(count, limit + 1)

    elements = _count(values.elements, limit)
    if elements == 1:
        return min(values.max_size - values.min_size + 1, limit + 1)
    # At least two choices: 2**limit.bit_length() already exceeds the cap.
    if values.min_size >= limit.bit_length():
        return limit + 1
    term = elements**values.min_size
    total = 0
    for _ in range(values.min_size, values.max_size + 1):
        total += term
        if total > limit:
            return limit + 1
        term *= elements
    return total


def _iterate(values: FiniteValues) -> Iterator[Any]:
    if isinstance(values, RecordValues):
        names = tuple(name for name, _ in values.fields)
        for combination in product(*(_iterate(value) for _, value in values.fields)):
            yield dict(zip(names, combination))
        return
    if not isinstance(values, ListValues):
        yield from values
        return
    for size in range(values.min_size, values.max_size + 1):
        # Independent pools construct fresh nested lists at each position.
        # Declared one_of leaf references keep their original identity.
        for combination in product(*(_iterate(values.elements) for _ in range(size))):
            yield list(combination)


def _check_choices(values: FiniteValues) -> None:
    """Require supported leaves even when a list's only value is empty."""
    if isinstance(values, ListValues):
        _check_choices(values.elements)
    elif isinstance(values, RecordValues):
        for _, value in values.fields:
            _check_choices(value)
    elif isinstance(values, tuple):
        for value in values:
            _check_value(value, set())


def _bounds(values: FiniteValues, path: str) -> dict[str, dict[str, int]]:
    if isinstance(values, RecordValues):
        bounds = {}
        for name, value in values.fields:
            field_path = (
                f"{path}.{name}"
                if name.isidentifier()
                else f"{path}[{json.dumps(name)}]"
            )
            bounds.update(_bounds(value, field_path))
        return bounds
    if not isinstance(values, ListValues):
        return {}
    return {
        path: {"min_size": values.min_size, "max_size": values.max_size},
        **_bounds(values.elements, path + "[]"),
    }


@dataclass(frozen=True)
class FinitePlan:
    domains: tuple[tuple[str, FiniteValues], ...]
    total: int
    list_bounds: dict[str, dict[str, int]]

    def arguments(self) -> Iterator[dict[str, Any]]:
        names = tuple(name for name, _ in self.domains)
        for combination in product(*(_iterate(values) for _, values in self.domains)):
            # Shared mutable declaration entries must not couple different cases.
            yield copy.deepcopy(dict(zip(names, combination)))


def finite_plan(specifications: dict[str, Any], *, limit: int) -> FinitePlan:
    domains = tuple((name, _values(value)) for name, value in specifications.items())
    total = 1
    for _, values in domains:
        total *= _count(values, limit)
        if total > limit:
            raise TypeError(f"exhaustive checking exceeds max_cases={limit}")
    for _, values in domains:
        _check_choices(values)
    # Check the budget before pooling ranges; validate references that coexist
    # in each case, including sharing across different generated parameters.
    for combination in product(*(_iterate(values) for _, values in domains)):
        containers: set[int] = set()
        for value in combination:
            _check_value(value, containers)
    bounds = {}
    for name, values in domains:
        bounds.update(_bounds(values, name))
    return FinitePlan(copy.deepcopy(domains), total, bounds)
