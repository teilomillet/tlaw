"""End-to-end probes for the mutation runner: real subprocess evidence.

These tests build a small boundary directory (one implementation module,
one laws file), then check the whole evidence loop: baseline first,
deterministic mutants, replay-confirmed kills, calibrated-timeout kills,
and ledger reclassification with stale entries kept visible.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tlaw.mutation import KillKind, MutationOutcome, generate_mutations, mutate


IMPLEMENTATION = (
    textwrap.dedent(
        """
        def double_all(values):
            return [value + value for value in values]


        def sign(value):
            if value < 0:
                return -1
            return 1
        """
    ).strip()
    + "\n"
)

# double_all is protected only by a deliberately weak length law, so its
# arithmetic mutant (value + value -> value - value) must survive. sign is
# protected by a discriminating law, so all of its mutants must die.
LAWS = (
    textwrap.dedent(
        """
        import tlaw

        from implementation import double_all, sign


        @tlaw.law
        def double_all_keeps_length(values: list[int]):
            return len(double_all(values)) == len(values)


        @tlaw.law
        def sign_matches_a_reference(value: int):
            return sign(value) == (-1 if value < 0 else 1)
        """
    ).strip()
    + "\n"
)


def _write_boundary(directory: Path, implementation: str, laws: str) -> None:
    (directory / "implementation.py").write_text(implementation, encoding="utf-8")
    (directory / "laws.py").write_text(laws, encoding="utf-8")


def test_weak_laws_leave_a_survivor_and_strong_laws_kill(tmp_path: Path) -> None:
    _write_boundary(tmp_path, IMPLEMENTATION, LAWS)

    result = mutate(tmp_path / "laws.py", tmp_path / "implementation.py")

    assert result.baseline_valid
    assert result.killed_count == 3
    assert result.survived_count == 1
    assert result.runnable_total == 4

    survivors = [run for run in result.runs if run.outcome is MutationOutcome.SURVIVED]
    assert survivors[0].mutation.qualname == "double_all"
    assert survivors[0].mutation.operator == "replace-arithmetic-operator"

    for run in result.runs:
        if run.outcome is MutationOutcome.KILLED:
            # Rule 3: every kill was reproduced in a second fresh run.
            assert "replay confirmed" in run.note
            # The exact rejecting law is recorded for later attribution.
            assert any("sign_matches_a_reference" in law for law in run.failed_tests)

    text = result.render_text()
    assert "%" not in text
    assert result.statement in text


def test_mutation_ignores_pytest_addopts_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_boundary(tmp_path, IMPLEMENTATION, LAWS)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 6 --dist loadgroup")

    result = mutate(tmp_path / "laws.py", tmp_path / "implementation.py")

    assert result.baseline_valid
    assert result.error_count == 0
    assert "unrecognized arguments" not in result.baseline.output


def test_the_ledger_explains_survivors_and_flags_stale_entries(
    tmp_path: Path,
) -> None:
    _write_boundary(tmp_path, IMPLEMENTATION, LAWS)

    # IDs are content-based and generation is pure, so the ledger can be
    # written before any run — the same way a reviewer would commit it.
    mutations = generate_mutations(IMPLEMENTATION, "implementation")
    survivor_id = next(
        m.mutation_id
        for m in mutations
        if m.qualname == "double_all" and m.operator == "replace-arithmetic-operator"
    )
    killed_id = next(
        m.mutation_id for m in mutations if m.operator == "replace-returned-constant"
    )
    ledger = tmp_path / "equivalents.toml"
    ledger.write_text(
        "version = 1\n\n"
        f"[equivalent.{survivor_id}]\n"
        'reason = "invisible to the declared length-only observation"\n\n'
        f"[equivalent.{killed_id}]\n"
        'reason = "wrongly claimed equivalent"\n\n'
        "[equivalent.ffffffffffff]\n"
        'reason = "no such mutant"\n',
        encoding="utf-8",
    )

    result = mutate(
        tmp_path / "laws.py",
        tmp_path / "implementation.py",
        equivalents=ledger,
    )

    assert result.survived_count == 0
    assert result.unresolved_equivalent_count == 1
    assert result.has_errors is False

    # A ledger claim contradicted by evidence stays visible, and the
    # unresolved equivalent remains in the report and the denominator.
    stale_ids = {mutation_id for mutation_id, _ in result.stale_ledger_entries}
    assert stale_ids == {killed_id, "ffffffffffff"}
    assert result.runnable_total == 4
    text = result.render_text()
    assert "STALE LEDGER ENTRY" in text
    assert "Unresolved equivalent candidates: 1" in text


COUNTDOWN = (
    textwrap.dedent(
        """
        def countdown(value):
            while value > 0:
                value = value - 1
            return value
        """
    ).strip()
    + "\n"
)

COUNTDOWN_LAWS = (
    textwrap.dedent(
        """
        import tlaw

        from implementation import countdown


        @tlaw.law
        def countdown_reaches_zero(value: int):
            bounded = abs(value) % 100
            return countdown(bounded) == 0
        """
    ).strip()
    + "\n"
)


def test_a_calibrated_timeout_counts_as_a_kill(tmp_path: Path) -> None:
    _write_boundary(tmp_path, COUNTDOWN, COUNTDOWN_LAWS)

    result = mutate(
        tmp_path / "laws.py",
        tmp_path / "implementation.py",
        timeout_floor_seconds=1.0,
        timeout_multiplier=2.0,
    )

    assert result.baseline_valid
    # Rule 2: the budget is derived from the measured baseline.
    assert result.budget_seconds == pytest.approx(
        max(1.0, 2.0 * result.baseline.duration_seconds)
    )
    # All three countdown mutants are behavioral: two become infinite
    # loops (timeout kills), one returns the wrong value (ordinary kill).
    assert result.killed_count == 3
    assert result.survived_count == 0

    timeout_kills = [run for run in result.runs if run.timed_out]
    assert timeout_kills
    for run in timeout_kills:
        assert run.outcome is MutationOutcome.KILLED
        assert run.kill_kind is KillKind.TIMEOUT
        assert "calibrated timeout" in run.note
        # Rule 3: the retry is timed against a baseline measured next to
        # it, not the one measured before the mutant loop began.
        assert "replay confirmed against a baseline re-measured" in run.note
        # A timeout kill names no law, so it must carry no attribution.
        assert run.failed_tests == ()

    # Rule 2: kills nothing reported stay countable on their own, so the
    # numerator cannot borrow weight from them.
    assert result.killed_by_timeout_count == len(timeout_kills)
    observed = [
        run
        for run in result.runs
        if run.outcome is MutationOutcome.KILLED and run.kill_kind is KillKind.OBSERVED
    ]
    assert len(observed) == result.killed_count - result.killed_by_timeout_count
    assert all(run.failed_tests for run in observed)

    rendered = result.render_text()
    assert (
        "Kills by calibrated timeout (no law attribution):"
        f" {result.killed_by_timeout_count}" in rendered
    )
    assert result.to_dict()["summary"]["killed_by_timeout"] == len(timeout_kills)
