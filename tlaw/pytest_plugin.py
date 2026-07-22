"""Small pytest reporting hooks used by ``tlaw check``."""

from __future__ import annotations

import os
from typing import Any

import hypothesis


EXPLAIN_ENV = "TLAW_EXPLAIN"


def pytest_collection_finish(session: Any) -> None:
    """Explain inferred generation only when the user asks for it."""
    if os.environ.get(EXPLAIN_ENV) != "1":
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
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
    for description, inputs, max_examples, deterministic in laws:
        if deterministic and isinstance(max_examples, int):
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
