"""Small pytest reporting hooks used by ``tlaw check``."""

from __future__ import annotations

import os
from typing import Any

import hypothesis
import pytest

from .replay import MODE_ENV, REPLAY_ENV, SEED_ENV, replay_names

EXPLAIN_ENV = "TLAW_EXPLAIN"


def pytest_collection_modifyitems(
    session: Any,
    config: Any,
    items: list[Any],
) -> None:
    """Replay only retained tlaw failures, never neighboring tests."""
    del session, config
    if os.environ.get(REPLAY_ENV):
        items[:] = [
            item
            for item in items
            if getattr(getattr(item, "obj", None), "__tlaw_replay_identity__", None)
        ]


def pytest_collection_finish(session: Any) -> None:
    """Explain generation and verify that replay found every retained law."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if os.environ.get(REPLAY_ENV):
        expected = set(replay_names())
        collected = {
            getattr(getattr(item, "obj", None), "__tlaw_replay_identity__", None)
            for item in session.items
        }
        collected.discard(None)
        missing = tuple(sorted(expected - collected))
        session.config._tlaw_replay_missing = missing
        if reporter is not None:
            reporter.write_line(
                f"tlaw replay: {len(collected)} of {len(expected)} "
                "retained laws collected"
            )

    if os.environ.get(EXPLAIN_ENV) != "1" or reporter is None:
        return

    laws: list[tuple[str, tuple[str, ...], int | None, bool]] = []
    for item in session.items:
        function = getattr(item, "obj", None)
        inputs = getattr(function, "__tlaw_inputs__", None)
        if not inputs:
            continue
        laws.append(
            (
                getattr(function, "__tlaw_description__", item.name),
                inputs,
                getattr(function, "__tlaw_max_examples__", None),
                bool(getattr(function, "__tlaw_deterministic__", False)),
            )
        )

    if not laws:
        return

    reporter.write_line(f"tlaw generation (Hypothesis {hypothesis.__version__}):")
    reporter.write_line("  pytest plugins: third-party autoload disabled")
    reporter.write_line("  pytest addopts: project addopts not applied")
    for description, inputs, max_examples, deterministic in laws:
        if os.environ.get(MODE_ENV) == "search" and isinstance(max_examples, int):
            posture = (
                f"up to {max_examples} examples; randomized search, "
                f"seed {os.environ.get(SEED_ENV)}; no example database"
            )
        elif os.environ.get(MODE_ENV) == "search":
            posture = (
                f"explicit strategies; search seed {os.environ.get(SEED_ENV)}; "
                "Hypothesis settings decide examples and phases; "
                "no input-mutation guard"
            )
        elif deterministic and isinstance(max_examples, int):
            posture = (
                f"up to {max_examples} examples; deterministic, no example database"
            )
        else:
            # The explicit form defers to ordinary Hypothesis settings.
            # That is a real difference in evidence posture — randomized
            # generation and no input-mutation guard — and it must be
            # stated where the user is already looking, not discovered.
            posture = (
                "explicit strategies; Hypothesis settings decide examples "
                "and randomization; no input-mutation guard"
            )
        reporter.write_line(f"  {description}: {', '.join(inputs)}; {posture}")


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """A missing retained law is broken evidence, even if others replayed."""
    del exitstatus
    missing = getattr(session.config, "_tlaw_replay_missing", ())
    if not missing:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            "tlaw replay could not find retained laws: " + ", ".join(missing)
        )
    session.exitstatus = pytest.ExitCode.USAGE_ERROR
