"""Laws for the canary: the default guard against unwired mutation evidence.

The canary exists for the user who has never heard of it: laws that
import an installed package instead of the boundary module would let
every mutant falsely "survive". The canary converts that silent false
signal into loud INVALID EVIDENCE, by default, with no opt-out.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tlaw.mutation import (
    BaselineRun,
    CanaryRun,
    Mutation,
    MutantRun,
    MutationOutcome,
    MutationResult,
    mutate,
    poison_source,
)


SOURCE = textwrap.dedent(
    """
    import math


    def double(value):
        return value * 2


    class Calculator:
        def add(self, left, right):
            return left + right
    """
).strip()


def test_poisoned_functions_raise_and_the_module_still_imports() -> None:
    poisoned = poison_source(SOURCE)
    namespace: dict[str, object] = {}
    exec(compile(poisoned, "<poisoned>", "exec"), namespace)  # imports cleanly

    with pytest.raises(RuntimeError, match="tlaw canary"):
        namespace["double"](2)
    with pytest.raises(RuntimeError, match="tlaw canary"):
        namespace["Calculator"]().add(1, 2)


@given(exit_code=st.integers(min_value=0, max_value=6), timed_out=st.booleans())
def test_the_canary_verdict_is_total_and_conservative(
    exit_code: int, timed_out: bool
) -> None:
    canary = CanaryRun(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=1.0,
        note="fixed evidence",
    )

    if timed_out or exit_code == 1:
        # A failure or a divergence from the passing baseline proves the
        # laws observed the poisoned module.
        assert canary.wired is True
    elif exit_code == 0:
        assert canary.wired is False
    else:
        # Harness trouble: wiring is unverified, never assumed.
        assert canary.wired is None


def test_an_unkilled_canary_invalidates_the_evidence() -> None:
    mutation = Mutation(
        mutation_id="id0000",
        operator="replace-arithmetic-operator",
        qualname="f",
        description="f: replace a + b with a - b",
        diff="",
        mutated_source="",
    )
    result = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False),
        runs=(MutantRun(mutation=mutation, outcome=MutationOutcome.ERROR, note=""),),
        canary=CanaryRun(
            exit_code=0, timed_out=False, duration_seconds=1.0, note="unwired"
        ),
    )

    assert result.has_errors is True
    assert "INVALID EVIDENCE" in result.render_text()


UNWIRED_IMPLEMENTATION = textwrap.dedent(
    """
    def add(left, right):
        return left + right
    """
).strip()

# These laws never import implementation.py: they re-implement the
# behavior locally, which is exactly the unwired shape the canary exists
# to catch — laws would pass against any mutant of the sibling file.
UNWIRED_LAWS = textwrap.dedent(
    """
    import tlaw


    def local_add(left, right):
        return left + right


    @tlaw.law
    def addition_is_commutative(left: int, right: int):
        return local_add(left, right) == local_add(right, left)
    """
).strip()


def test_unwired_laws_are_reported_as_invalid_evidence_not_survivors(
    tmp_path: Path,
) -> None:
    (tmp_path / "implementation.py").write_text(
        UNWIRED_IMPLEMENTATION + "\n", encoding="utf-8"
    )
    (tmp_path / "laws.py").write_text(UNWIRED_LAWS + "\n", encoding="utf-8")

    result = mutate(tmp_path / "laws.py", tmp_path / "implementation.py")

    assert result.baseline_valid
    assert result.canary is not None
    assert result.canary.wired is False
    assert result.has_errors is True
    # The false signal this prevents: zero survivors are reported,
    # because nothing was scored at all.
    assert result.survived_count == 0
    assert result.killed_count == 0
    assert all(run.outcome is MutationOutcome.ERROR for run in result.runs)
    assert all("canary" in run.note for run in result.runs)

    text = result.render_text()
    assert "INVALID EVIDENCE" in text
    assert "from implementation import" in text
