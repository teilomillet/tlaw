"""One explicit comparison for laws: ``tlaw.same``.

What
----
``same(left, right, within=..., relative=...)`` compares two values
structurally — numbers with explicit absolute and relative tolerances,
strings exactly, lists, tuples, and dicts element by element, and
array-likes (NumPy, and any object exposing ``shape``/``dtype``/``tolist``)
by converting them to nested lists first. It returns a ``Comparison`` that
is truthy when the values agree and that retains both sides and the first
difference for the failure message.

Why
---
The retrain probe verified two bottlenecks with one shape: exact Python
``==`` is the wrong comparison for approximate floats, and NumPy ``==``
returns an array instead of one boolean, which tlaw honestly rejects.
Meanwhile a bare boolean law forgets the values that produced ``False``.
Owning the comparison solves both at once: the tolerance becomes visible
law text instead of a hidden judgment call, and the failure output can
show ``left``, ``right``, and exactly where they diverged.

How
---
- ``within`` (absolute) and ``relative`` (a fraction of the larger
  magnitude) both default to ``0.0`` (exact) and combine like
  ``math.isclose``: ``gap <= max(within, relative * max(|a|, |b|))``.
  ``within`` is correct near zero, where relative tolerance is meaningless
  because the magnitude is zero; ``relative`` is correct for values whose
  scale is large or unknown, where a fixed absolute tolerance is either
  uselessly strict or falsely loose. Tolerance is part of the contract,
  so there is no silent default: omit both and you asked for exact.
- ``NaN`` never compares equal, and the reason says so. A pipeline that
  needs NaN-equality is a new, explicit decision after a real probe, not
  a silent default.
- ``bool`` compares only with ``bool``: ``True == 1`` in Python would
  let a type change hide inside a passing law.
- Array-likes are recognized by duck type (``shape``, ``dtype``,
  ``tolist``) so tlaw needs no NumPy dependency and the same rule covers
  other array libraries. Their explicit shapes are compared before
  ``tolist`` conversion, because different empty shapes can both become
  the same empty list; non-empty value differences still surface at a
  named path.
- Anything unrecognized falls back to ``==`` — and if ``==`` does not
  produce one Python boolean, that is reported as a difference with the
  reason stated, never guessed (interface acceptance rule 6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable

from .claims import Claim, _short


def _array_shape(value: Any) -> tuple[Any, ...] | None:
    """Return an array-like's explicit shape without importing its library."""
    if not (
        hasattr(value, "shape")
        and hasattr(value, "dtype")
        and callable(getattr(value, "tolist", None))
    ):
        return None
    try:
        return tuple(value.shape)
    except (TypeError, ValueError):
        return None


def _normalized(value: Any) -> Any:
    """Convert array-likes to nested lists; leave everything else alone.

    Duck-typed on ``shape``/``dtype``/``tolist`` so NumPy arrays, NumPy
    scalars, and torch-style tensors all become plain nested Python
    values without tlaw importing any of those libraries. If ``tolist``
    itself fails, the original value is kept and the fallback ``==``
    branch will report honestly.
    """
    if (
        hasattr(value, "shape")
        and hasattr(value, "dtype")
        and callable(getattr(value, "tolist", None))
    ):
        try:
            return value.tolist()
        except Exception:
            return value
    return value


@dataclass(frozen=True)
class Difference:
    """The first place two compared values diverge, and why."""

    path: str
    left: Any
    right: Any
    reason: str


@dataclass(frozen=True)
class Comparison(Claim):
    """The result of ``same``: truthy on agreement, explanatory on failure.

    Returned instead of a bare bool so a failing law can show both sides
    and the first difference — retrain probe bottleneck 3. ``__bool__``
    makes it usable in plain ``assert`` and boolean expressions.
    ``and_then`` composes another claim without paying the explanation loss
    of ``and``/``or``.
    """

    left: Any
    right: Any
    within: float
    difference: Difference | None
    relative: float = 0.0

    @property
    def matched(self) -> bool:
        return self.difference is None

    def explain(self) -> str:
        """Render both sides and the first difference as readable text."""
        if self.difference is None:
            return "values are the same"
        where = self.difference
        lines = [
            f"first difference at value{where.path}:",
            f"  left:  {_short(where.left)}",
            f"  right: {_short(where.right)}",
            f"  {where.reason}",
        ]
        if where.path:
            lines.append(f"left:  {_short(self.left)}")
            lines.append(f"right: {_short(self.right)}")
        return "\n".join(lines)

    def and_then(self, observe: Callable[[], Any]) -> Any:
        """State another claim after this comparison has matched.

        An equivalence law may need a second claim — for example, that two
        matching runs also produced the expected amount of work. Writing the
        claims with ``and`` converts the first comparison to a bare boolean,
        so a failure reports only ``False``. ``and_then`` keeps the first
        comparison when it fails and otherwise returns the next claim::

            return tlaw.same(refactored, baseline).and_then(
                lambda: tlaw.same(
                    (refactored_steps, baseline_steps),
                    (expected_steps, expected_steps),
                )
            )

        ``observe`` takes no argument because a comparison relates two values
        rather than producing one distinguished payload. Close over either
        value when the next claim needs it. The next result may be another
        ``Comparison``, an ``Exclusion``, a ``Refusal``, an ``Outcome``, or a
        bool.
        """
        return self._continue_claim(observe)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _numeric_gap_and_allowance(
    left: int | float,
    right: int | float,
    within: float,
    relative: float,
) -> tuple[bool, int | float | Fraction, int | float | Fraction]:
    """Compare finite numbers without losing arbitrary-size Python ints.

    Ordinary arithmetic keeps the familiar float behavior. Python only
    raises here when a very large integer cannot be converted to a float;
    in that case fractions preserve the exact values and the documented
    tolerance equation.
    """
    try:
        gap = abs(left - right)
        allowance = max(within, relative * max(abs(left), abs(right)))
    except OverflowError:
        exact_left = Fraction(left)
        exact_right = Fraction(right)
        gap = abs(exact_left - exact_right)
        allowance = max(
            Fraction(within),
            Fraction(relative) * max(abs(exact_left), abs(exact_right)),
        )
    return gap <= allowance, gap, allowance


def _first_difference(
    left: Any,
    right: Any,
    within: float,
    relative: float,
    path: str,
    seen_pairs: set[tuple[int, int]],
) -> Difference | None:
    left_shape = _array_shape(left)
    right_shape = _array_shape(right)
    if left_shape is not None and right_shape is not None and left_shape != right_shape:
        # ``tolist()`` preserves ordinary nested shape, but both (0, 2)
        # and (0, 3) become []. Compare the declared shapes first so an
        # empty dimension cannot erase a structural difference.
        return Difference(
            path,
            left,
            right,
            f"array shapes differ: {left_shape!r} vs {right_shape!r}",
        )

    left = _normalized(left)
    right = _normalized(right)

    # bool before numbers: bool subclasses int, and True == 1 would let a
    # flag silently become a count inside a passing law.
    if isinstance(left, bool) or isinstance(right, bool):
        if isinstance(left, bool) and isinstance(right, bool):
            if left is right:
                return None
            return Difference(path, left, right, "boolean values differ")
        return Difference(
            path,
            left,
            right,
            "bool compared with non-bool; True == 1 would hide a type change",
        )

    if _is_number(left) and _is_number(right):
        if left == right:  # also covers equal infinities exactly
            return None
        if isinstance(left, float) and math.isnan(left):
            return Difference(
                path, left, right, "left is NaN; NaN never compares equal"
            )
        if isinstance(right, float) and math.isnan(right):
            return Difference(
                path, left, right, "right is NaN; NaN never compares equal"
            )
        if (isinstance(left, float) and math.isinf(left)) or (
            isinstance(right, float) and math.isinf(right)
        ):
            # Not exactly equal (that returned above), and no finite
            # tolerance closes a gap to infinity. Relative tolerance times
            # infinity is nonsense, so infinities are handled explicitly
            # rather than falling into the tolerance arithmetic.
            return Difference(
                path, left, right, "one value is infinite and they are not equal"
            )
        # Combined tolerance, the math.isclose model: a gap passes if it is
        # within the absolute floor OR within the relative allowance scaled
        # to the larger magnitude. Absolute alone cannot express "equal to a
        # few ULP" when the magnitude is unknown; relative alone is
        # meaningless at zero, where max(|left|,|right|) is zero. Each
        # covers the other's blind spot.
        matched, gap, allowance = _numeric_gap_and_allowance(
            left, right, within, relative
        )
        if matched:
            return None
        detail = f"absolute difference {_short(gap)} exceeds within={within!r}"
        if relative:
            detail += (
                f" and relative={relative!r} "
                f"(allowance {_short(allowance)} at this magnitude)"
            )
        return Difference(path, left, right, detail)

    # str/bytes before generic sequences: strings are sequences too, and
    # comparing them character by character would produce noise.
    if isinstance(left, str | bytes) or isinstance(right, str | bytes):
        if type(left) is type(right) and left == right:
            return None
        return Difference(path, left, right, "values differ")

    if isinstance(left, list | tuple) and isinstance(right, list | tuple):
        if len(left) != len(right):
            return Difference(
                path,
                left,
                right,
                f"lengths differ: {len(left)} vs {len(right)}",
            )
        pair = (id(left), id(right))
        if pair in seen_pairs:
            return None
        # Keep established pairs for the whole comparison. Re-visiting the
        # same two objects is either a cycle or shared structure; their value
        # relation cannot change during this pure traversal, so memoization is
        # both the termination rule and the structural-value semantics.
        seen_pairs.add(pair)
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            found = _first_difference(
                left_item,
                right_item,
                within,
                relative,
                f"{path}[{index}]",
                seen_pairs,
            )
            if found is not None:
                return found
        return None

    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            missing = sorted(repr(key) for key in left.keys() - right.keys())
            extra = sorted(repr(key) for key in right.keys() - left.keys())
            detail = []
            if missing:
                detail.append(f"only in left: {', '.join(missing)}")
            if extra:
                detail.append(f"only in right: {', '.join(extra)}")
            return Difference(path, left, right, f"keys differ ({'; '.join(detail)})")
        pair = (id(left), id(right))
        if pair in seen_pairs:
            return None
        seen_pairs.add(pair)
        for key in left:
            found = _first_difference(
                left[key],
                right[key],
                within,
                relative,
                f"{path}[{key!r}]",
                seen_pairs,
            )
            if found is not None:
                return found
        return None

    # Fallback: ordinary equality — but only one Python boolean counts.
    # Refusing to interpret anything else is the same honesty rule the
    # law decorator applies to law return values.
    try:
        equal = left == right
    except Exception as error:
        return Difference(path, left, right, f"comparing raised {error!r}")
    if not isinstance(equal, bool):
        return Difference(
            path,
            left,
            right,
            f"tlaw cannot interpret == for {type(left).__name__}; "
            "it did not produce one Python boolean",
        )
    if equal:
        return None
    return Difference(path, left, right, "values differ")


def _tolerance(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def same(
    left: Any, right: Any, *, within: float = 0.0, relative: float = 0.0
) -> Comparison:
    """Compare two values structurally with visible numeric tolerances.

    Returns a ``Comparison``: truthy when the values agree, and carrying
    both sides plus the first difference when they do not. A law can
    return it directly — ``tlaw check`` will render the retained values —
    or use it in a plain ``assert``.

    Tolerances apply to numeric leaves only; everything else is compared
    exactly. Two numbers agree when their gap is within *either*:

    - ``within`` — an **absolute** tolerance, the right choice near zero
      (advantages, residuals, centered rewards), where relative tolerance
      is meaningless because the magnitude is zero; or
    - ``relative`` — a fraction of the larger magnitude, the right choice
      for values whose scale is unknown or large, where a fixed absolute
      tolerance is either uselessly strict or falsely loose. This is the
      ``math.isclose`` model: ``gap <= max(within, relative * max(|a|,
      |b|))``.

    Both default to ``0.0`` (exact), and both are part of the law's
    contract, so an impossible tolerance is rejected rather than guessed.
    Comparing against zero needs ``within``: ``relative`` alone can never
    close a gap to zero.
    """
    within_value = _tolerance(within, name="within")
    relative_value = _tolerance(relative, name="relative")
    return Comparison(
        left=left,
        right=right,
        within=within_value,
        relative=relative_value,
        difference=_first_difference(
            left, right, within_value, relative_value, "", set()
        ),
    )
