"""Real core entry points and deliberately wrong alternatives for the audit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import tlaw
from tlaw.category import Arrow, arrow, identity
from tlaw.replay import decode_value, encode_value


class Core:
    encode = staticmethod(encode_value)
    decode = staticmethod(decode_value)
    same = staticmethod(tlaw.same)
    integer = staticmethod(tlaw.integer)
    list_of = staticmethod(tlaw.list_of)
    depends = staticmethod(tlaw.depends)
    arrow = staticmethod(arrow)
    identity = staticmethod(identity)
    system = staticmethod(tlaw.system)

    # An audit also substitutes its target as a System's constructor.
    # This tiny counter gives every target the same mutable path fixture.
    def __init__(self, value: int) -> None:
        self.value = value

    def add(self, amount: int) -> None:
        self.value += amount


class LosesIntegerSign(Core):
    @staticmethod
    def encode(value: Any) -> dict[str, Any]:
        return encode_value(abs(value) if type(value) is int else value)


class CollapsesTuples(Core):
    @staticmethod
    def decode(encoded: dict[str, Any]) -> Any:
        value = decode_value(encoded)
        return list(value) if isinstance(value, tuple) else value


class ShiftedWireIntegers(Core):
    # The two errors cancel in a round trip. The wire-format law must catch it.
    @staticmethod
    def encode(value: Any) -> dict[str, Any]:
        return encode_value(value + 1 if type(value) is int else value)

    @staticmethod
    def decode(encoded: dict[str, Any]) -> Any:
        value = decode_value(encoded)
        return value - 1 if type(value) is int else value


class AlwaysEqual(Core):
    @staticmethod
    def same(left: Any, right: Any, **tolerances: float) -> tlaw.Comparison:
        return tlaw.same(left, left, **tolerances)


class IgnoresTolerance(Core):
    @staticmethod
    def same(left: Any, right: Any, **tolerances: float) -> tlaw.Comparison:
        return tlaw.same(left, right)


class WidensIntegerDomain(Core):
    @staticmethod
    def integer(low: int, high: int) -> tlaw.Domain:
        return tlaw.integer(low - 1, high + 1)


class PermitsEmptyLists(Core):
    @staticmethod
    def list_of(
        element: tlaw.Domain | type, *, min_size: int = 0, max_size: int | None = None
    ) -> tlaw.Domain:
        return tlaw.list_of(element, min_size=0, max_size=max_size)


class ShiftsDependency(Core):
    @staticmethod
    def depends(builder: Callable[[int], tlaw.Domain]) -> tlaw.DependentDomain:
        return tlaw.depends(lambda budget: builder(budget + 1))


class _LastStepOnly(Arrow):
    def then(self, following: Arrow) -> Arrow:
        return following


class DropsComposedStep(Core):
    @staticmethod
    def arrow(function: Callable, *, name: str | None = None) -> Arrow:
        return _LastStepOnly(boxes=arrow(function, name=name).boxes)


class ReusesState(Core):
    @staticmethod
    def system(implementation: Callable, *, observe: Callable) -> tlaw.System:
        shared: Core | None = None

        def start(target: type[Core], value: int) -> Core:
            nonlocal shared
            if shared is None:
                shared = target(value)
            return shared

        return tlaw.system(implementation, start=start, observe=observe)
