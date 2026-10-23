"""Composition follows written order; every path execution constructs fresh state."""

import os

import tlaw
from tlaw.audit import TARGET_ENV
from tlaw.target import load_target


subject = load_target(os.environ.get(TARGET_ENV, "implementations:Core"))
Small = tlaw.integer(-20, 20)


@tlaw.law
def composition_runs_steps_in_declared_order(
    value: Small, offset: Small, factor: Small
):
    add = subject.arrow(lambda number: number + offset, name="add")
    multiply = subject.arrow(lambda number: number * factor, name="multiply")
    return (add >> multiply)(value) == (value + offset) * factor


@tlaw.law
def identity_and_grouping_preserve_results(value: Small):
    add = subject.arrow(lambda number: number + 3, name="add")
    multiply = subject.arrow(lambda number: number * 2, name="multiply")
    subtract = subject.arrow(lambda number: number - 1, name="subtract")
    expected = (value + 3) * 2 - 1
    return (
        (subject.identity() >> add)(value) == value + 3
        and (add >> subject.identity())(value) == value + 3
        and ((add >> multiply) >> subtract)(value) == expected
        and (add >> (multiply >> subtract))(value) == expected
    )


@tlaw.law
def paths_start_fresh_on_every_execution(initial: Small, amount: Small):
    counter = subject.system(subject, observe=lambda state: state.value)
    start = counter(initial)
    changed = start.call("add", amount)
    return (
        changed.result() == initial + amount
        and changed.result() == initial + amount
        and start.result() == initial
    )
