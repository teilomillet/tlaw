"""Generated values must satisfy the bounds written independently in each law."""

import os

import tlaw
from tlaw.audit import TARGET_ENV
from tlaw.target import load_target


subject = load_target(os.environ.get(TARGET_ENV, "implementations:Core"))
Bounded = subject.integer(-3, 5)
Nonempty = subject.list_of(Bounded, min_size=1, max_size=4)
Budget = subject.integer(1, 8)
Lengths = subject.depends(
    lambda budget: subject.list_of(subject.integer(1, budget), min_size=1, max_size=4)
)


@tlaw.law
def integer_generation_respects_declared_bounds(value: Bounded):
    return type(value) is int and -3 <= value <= 5


@tlaw.law
def list_generation_respects_declared_size(values: Nonempty):
    return 1 <= len(values) <= 4 and all(
        type(value) is int and -3 <= value <= 5 for value in values
    )


@tlaw.law
def dependent_values_fit_their_budget(budget: Budget, lengths: Lengths):
    return (
        1 <= budget <= 8
        and 1 <= len(lengths) <= 4
        and all(1 <= length <= budget for length in lengths)
    )
