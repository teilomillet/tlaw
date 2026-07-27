"""Laws for the pure mutation-evidence core: no subprocesses here.

These properties pin the three encoded evidence rules where they are
cheapest to falsify: outcome classification, content-based mutation
identity, ledger honesty, and the narrow report statement.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tlaw import mutation as mutation_module
from tlaw.audit import ManifestError
from tlaw.mutation import (
    BaselineRun,
    Boundary,
    KillKind,
    Mutation,
    MutantRun,
    MutationOutcome,
    MutationResult,
    apply_equivalence_ledger,
    classify_mutant_exit,
    generate_mutations,
    load_equivalence_ledger,
)


SOURCE = textwrap.dedent(
    """
    def compute(a, b):
        if a < b:
            log(a)
            return True
        total = a + b
        while total > 0:
            total = total - 1
        return 1
    """
).strip()


@given(exit_code=st.integers(min_value=0, max_value=6), timed_out=st.booleans())
def test_classification_attributes_by_difference_from_baseline(
    exit_code: int, timed_out: bool
) -> None:
    outcome, kill_kind, note = classify_mutant_exit(exit_code, timed_out=timed_out)

    if timed_out:
        # An infinite loop is behavior, not infrastructure: the budget is
        # always calibrated against the measured baseline. No law reported
        # it, so the kind records that this kill names nothing.
        assert outcome is MutationOutcome.KILLED
        assert kill_kind is KillKind.TIMEOUT
    elif exit_code == 0:
        assert outcome is MutationOutcome.SURVIVED
    elif exit_code == 1:
        # A False comparison and an implementation exception are both
        # behavioral differences the clean baseline did not show.
        assert outcome is MutationOutcome.KILLED
        assert kill_kind is KillKind.OBSERVED
    elif exit_code == 2:
        # The baseline collected these laws, so a collection failure means
        # the mutant could not be loaded: never a kill, never a success.
        assert outcome is MutationOutcome.INVALID
    else:
        assert outcome is MutationOutcome.ERROR
    assert isinstance(note, str) and note
    # Only a kill has a kind: nothing else may carry one.
    assert (kill_kind is None) is (outcome is not MutationOutcome.KILLED)


def test_the_first_boundary_operators_each_produce_their_mutation() -> None:
    mutations = generate_mutations(SOURCE, "implementation")
    descriptions = {mutation.operator: mutation.description for mutation in mutations}

    assert "replace-arithmetic-operator" in descriptions
    assert "alter-comparison-boundary" in descriptions
    assert "replace-returned-constant" in descriptions
    assert "skip-call-statement" in descriptions
    assert "negate-condition" in descriptions

    all_descriptions = " | ".join(m.description for m in mutations)
    assert "replace a + b with a - b" in all_descriptions
    assert "replace a < b with a <= b" in all_descriptions
    assert "replace return True with return False" in all_descriptions
    assert "replace return 1 with return 2" in all_descriptions
    assert "replace log(a) with pass" in all_descriptions
    assert "replace total > 0 with not total > 0" in all_descriptions


def test_mutation_ids_survive_unrelated_edits_elsewhere_in_the_module() -> None:
    # The equivalence ledger references IDs from ordinary commits, so an
    # ID must not change when someone adds code above the function.
    base_ids = {m.mutation_id for m in generate_mutations(SOURCE, "implementation")}
    extended_source = "def unrelated():\n    return 3\n\n\n" + SOURCE
    extended = generate_mutations(extended_source, "implementation")
    extended_ids = {m.mutation_id for m in extended if m.qualname == "compute"}

    assert base_ids == extended_ids


def test_generation_is_deterministic_with_unique_ids() -> None:
    first = generate_mutations(SOURCE, "implementation")
    second = generate_mutations(SOURCE, "implementation")

    assert first == second
    identifiers = [mutation.mutation_id for mutation in first]
    assert len(identifiers) == len(set(identifiers))


def test_every_mutant_compiles_and_differs_from_the_normalized_original() -> None:
    normalized = ast.unparse(ast.parse(SOURCE))
    for mutation in generate_mutations(SOURCE, "implementation"):
        compile(mutation.mutated_source, "<mutant>", "exec")
        assert mutation.mutated_source != normalized
        assert mutation.diff


def _run(identifier: str, outcome: MutationOutcome) -> MutantRun:
    mutation = Mutation(
        mutation_id=identifier,
        operator="replace-arithmetic-operator",
        qualname="f",
        description="f: replace a + b with a - b",
        diff="",
        mutated_source="",
    )
    return MutantRun(mutation=mutation, outcome=outcome, note="fixed evidence")


@given(outcomes=st.lists(st.sampled_from(list(MutationOutcome)), max_size=8))
def test_the_ledger_reclassifies_only_survivors_and_drops_nothing(
    outcomes: list[MutationOutcome],
) -> None:
    runs = tuple(
        _run(f"id{index:04d}", outcome) for index, outcome in enumerate(outcomes)
    )
    ledger = {f"id{index:04d}": "declared reason" for index in range(len(outcomes))}
    ledger["ffffffffffff"] = "phantom entry"

    updated, stale = apply_equivalence_ledger(runs, ledger)

    assert len(updated) == len(runs)
    for before, after in zip(runs, updated):
        if before.outcome is MutationOutcome.SURVIVED:
            assert after.outcome is MutationOutcome.UNRESOLVED_EQUIVALENT
        else:
            # The ledger explains survivors; it cannot overrule evidence.
            assert after.outcome is before.outcome

    stale_ids = {mutation_id for mutation_id, _ in stale}
    assert "ffffffffffff" in stale_ids
    for index, outcome in enumerate(outcomes):
        if outcome is not MutationOutcome.SURVIVED:
            assert f"id{index:04d}" in stale_ids


@given(outcomes=st.lists(st.sampled_from(list(MutationOutcome)), max_size=10))
def test_the_report_shows_every_mutant_and_never_a_percentage(
    outcomes: list[MutationOutcome],
) -> None:
    runs = tuple(
        _run(f"id{index:04d}", outcome) for index, outcome in enumerate(outcomes)
    )
    result = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False),
        runs=runs,
        budget_seconds=10.0,
        timeout_multiplier=10.0,
        timeout_floor_seconds=5.0,
    )

    assert result.runnable_total == (
        result.killed_count + result.survived_count + result.unresolved_equivalent_count
    )
    text = result.render_text()
    assert result.statement in text
    assert "%" not in text
    for run in runs:
        assert run.mutation.mutation_id in text


def test_invalid_mutants_do_not_break_evidence_but_errors_do() -> None:
    baseline = BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False)
    invalid_only = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=baseline,
        runs=(_run("id0000", MutationOutcome.INVALID),),
    )
    with_error = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=baseline,
        runs=(_run("id0000", MutationOutcome.ERROR),),
    )
    bad_baseline = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=1, duration_seconds=1.0, timed_out=False),
        runs=(),
    )

    assert invalid_only.has_errors is False
    assert with_error.has_errors is True
    assert bad_baseline.has_errors is True


def test_the_ledger_file_is_validated_before_it_is_trusted(tmp_path) -> None:
    valid = tmp_path / "equivalents.toml"
    valid.write_text(
        'version = 1\n\n[equivalent.abc123def456]\nreason = "unobservable"\n',
        encoding="utf-8",
    )
    assert load_equivalence_ledger(valid) == {"abc123def456": "unobservable"}

    wrong_version = tmp_path / "wrong-version.toml"
    wrong_version.write_text("version = 2\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_equivalence_ledger(wrong_version)

    empty_reason = tmp_path / "empty-reason.toml"
    empty_reason.write_text(
        'version = 1\n\n[equivalent.abc123def456]\nreason = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ManifestError):
        load_equivalence_ledger(empty_reason)


# --- Rule 3: what counts as a kill replaying -------------------------------
#
# The replay guard used to accept any second run whose *label* was KILLED.
# That let two runs disagree about what happened and still record
# agreement: a timeout confirmed by a law failure, or a kill attributed to
# one law confirmed by a kill attributed to another. These pin the three
# ways a kill can now fail to replay, without any subprocess.


def _boundary(tmp_path) -> Boundary:
    return Boundary(
        source=tmp_path,
        module_relative=Path("implementation.py"),
        module_name="implementation",
        laws_directory=tmp_path,
        laws_name="laws.py",
    )


def _mutation() -> Mutation:
    return Mutation(
        mutation_id="abc123def456",
        operator="replace-arithmetic-operator",
        qualname="compute",
        description="compute: + -> -",
        diff="- a + b\n+ a - b",
        mutated_source="def compute(a, b):\n    return a - b\n",
    )


def _kill(kind: KillKind, *, failed_tests: tuple[str, ...] = ()) -> MutantRun:
    return MutantRun(
        mutation=_mutation(),
        outcome=MutationOutcome.KILLED,
        note="calibrated timeout" if kind is KillKind.TIMEOUT else "law failure",
        exit_code=None if kind is KillKind.TIMEOUT else 1,
        timed_out=kind is KillKind.TIMEOUT,
        failed_tests=failed_tests,
        kill_kind=kind,
    )


def _confirm(monkeypatch, tmp_path, first: MutantRun, replay: MutantRun) -> MutantRun:
    """Run the guard with a fixed replay result and a passing baseline."""
    monkeypatch.setattr(mutation_module, "_run_one_mutant", lambda *a, **k: replay)
    monkeypatch.setattr(
        mutation_module,
        "_run_pytest_in_copy",
        lambda *a, **k: mutation_module._PytestRun(
            exit_code=0, timed_out=False, duration_seconds=1.0, output="", command=()
        ),
    )
    return mutation_module._confirm_kill(
        _boundary(tmp_path),
        _mutation(),
        first,
        budget_seconds=10.0,
        baseline_duration=1.0,
        timeout_multiplier=10.0,
        timeout_floor_seconds=5.0,
        baseline_timeout_seconds=60.0,
    )


def test_a_timeout_is_not_confirmed_by_an_observed_kill(monkeypatch, tmp_path) -> None:
    # The two runs disagree about what happened. Agreement on the word
    # "killed" is not agreement on the evidence.
    confirmed = _confirm(
        monkeypatch,
        tmp_path,
        _kill(KillKind.TIMEOUT),
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::some_law",)),
    )

    assert confirmed.outcome is MutationOutcome.ERROR
    assert "did not replay in kind" in confirmed.note
    # An error is not a kill: it carries neither kind nor attribution.
    assert confirmed.kill_kind is None
    assert confirmed.failed_tests == ()


def test_an_observed_kill_is_not_confirmed_by_a_timeout(monkeypatch, tmp_path) -> None:
    confirmed = _confirm(
        monkeypatch,
        tmp_path,
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::some_law",)),
        _kill(KillKind.TIMEOUT),
    )

    assert confirmed.outcome is MutationOutcome.ERROR
    assert "did not replay in kind" in confirmed.note


def test_an_observed_kill_must_replay_with_the_same_laws(monkeypatch, tmp_path) -> None:
    # Which law defends a behavior is read out of failed_tests. Two runs
    # that name different laws leave that reading unsupported, even though
    # both said "killed".
    drifted = _confirm(
        monkeypatch,
        tmp_path,
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::first_law",)),
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::second_law",)),
    )

    assert drifted.outcome is MutationOutcome.ERROR
    assert "attribution did not replay" in drifted.note
    assert "first_law" in drifted.note and "second_law" in drifted.note

    # The same laws in a different order is the same attribution.
    reordered = _confirm(
        monkeypatch,
        tmp_path,
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::a", "laws.py::b")),
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::b", "laws.py::a")),
    )

    assert reordered.outcome is MutationOutcome.KILLED
    assert "replay confirmed" in reordered.note


def test_a_timeout_kill_is_recalibrated_against_a_fresh_baseline(
    monkeypatch, tmp_path
) -> None:
    # The budget came from a baseline measured before the mutant loop
    # began. If the machine became contended since, every remaining mutant
    # looks slow and two slow runs would confirm each other. A baseline
    # that no longer passes leaves no calibration at all.
    monkeypatch.setattr(
        mutation_module,
        "_run_pytest_in_copy",
        lambda *a, **k: mutation_module._PytestRun(
            exit_code=0, timed_out=True, duration_seconds=60.0, output="", command=()
        ),
    )
    monkeypatch.setattr(
        mutation_module,
        "_run_one_mutant",
        lambda *a, **k: pytest.fail("the mutant must not be retried without a budget"),
    )

    confirmed = mutation_module._confirm_kill(
        _boundary(tmp_path),
        _mutation(),
        _kill(KillKind.TIMEOUT),
        budget_seconds=10.0,
        baseline_duration=1.0,
        timeout_multiplier=10.0,
        timeout_floor_seconds=5.0,
        baseline_timeout_seconds=60.0,
    )

    assert confirmed.outcome is MutationOutcome.ERROR
    assert "re-measured baseline no longer passes" in confirmed.note
    assert confirmed.kill_kind is None


def test_a_confirmed_timeout_kill_records_the_fresh_calibration(
    monkeypatch, tmp_path
) -> None:
    seen: list[float] = []

    def fresh_baseline(*args, **kwargs):
        return mutation_module._PytestRun(
            exit_code=0, timed_out=False, duration_seconds=4.0, output="", command=()
        )

    def retry(*args, **kwargs):
        seen.append(kwargs["budget_seconds"])
        return _kill(KillKind.TIMEOUT)

    monkeypatch.setattr(mutation_module, "_run_pytest_in_copy", fresh_baseline)
    monkeypatch.setattr(mutation_module, "_run_one_mutant", retry)

    confirmed = mutation_module._confirm_kill(
        _boundary(tmp_path),
        _mutation(),
        _kill(KillKind.TIMEOUT),
        budget_seconds=10.0,
        baseline_duration=1.0,
        timeout_multiplier=10.0,
        timeout_floor_seconds=5.0,
        baseline_timeout_seconds=60.0,
    )

    # The retry is timed against the baseline as it is *now* (4.0s x 10),
    # not the stale one the first run used (1.0s x 10).
    assert seen == [40.0]
    assert confirmed.outcome is MutationOutcome.KILLED
    assert "re-measured at 4.00s" in confirmed.note


def test_a_timeout_kill_that_fits_the_fresh_budget_is_not_a_kill(
    monkeypatch, tmp_path
) -> None:
    # The mutant was never slow: the machine was. Under an honest budget
    # the laws pass, so there is no behavioral difference to count.
    survivor = MutantRun(
        mutation=_mutation(),
        outcome=MutationOutcome.SURVIVED,
        note="all laws passed",
        exit_code=0,
    )
    confirmed = _confirm(monkeypatch, tmp_path, _kill(KillKind.TIMEOUT), survivor)

    assert confirmed.outcome is MutationOutcome.ERROR
    assert "kill did not replay" in confirmed.note


def test_only_a_confirmed_observed_kill_ever_names_a_law(monkeypatch, tmp_path) -> None:
    # The invariant every report consumer may rely on: a run naming laws
    # is a confirmed observed kill. Downgraded runs move their first
    # run's attribution into the note, where it reads as history.
    downgraded = _confirm(
        monkeypatch,
        tmp_path,
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::first_law",)),
        _kill(KillKind.OBSERVED, failed_tests=("laws.py::second_law",)),
    )
    result = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False),
        runs=(downgraded, _kill(KillKind.TIMEOUT)),
    )

    for run in result.runs:
        if run.failed_tests:
            assert run.outcome is MutationOutcome.KILLED
            assert run.kill_kind is KillKind.OBSERVED
    # A timeout kill names nothing, so no rendered line may read as a law
    # having rejected it.
    assert "law:" not in result.render_text()


def test_timeout_kills_are_counted_apart_from_kills_that_name_a_law() -> None:
    result = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False),
        runs=(
            _kill(KillKind.OBSERVED, failed_tests=("laws.py::a_law",)),
            _kill(KillKind.TIMEOUT),
            _kill(KillKind.TIMEOUT),
        ),
    )

    assert result.killed_count == 3
    assert result.killed_by_timeout_count == 2
    # The narrow statement is unchanged: a timeout kill is still a kill.
    assert result.statement == "Killed 3 of 3 runnable generated mutations."
    # But the numerator stays decomposable, so it cannot quietly borrow
    # weight from kills that name nothing.
    rendered = result.render_text()
    assert "Kills by calibrated timeout (no law attribution): 2" in rendered
    assert result.to_dict()["summary"]["killed_by_timeout"] == 2


def test_a_report_without_timeout_kills_stays_silent_about_them() -> None:
    result = MutationResult(
        laws="laws.py",
        implementation="implementation.py",
        baseline=BaselineRun(exit_code=0, duration_seconds=1.0, timed_out=False),
        runs=(_kill(KillKind.OBSERVED, failed_tests=("laws.py::a_law",)),),
    )

    assert result.killed_by_timeout_count == 0
    assert "calibrated timeout" not in result.render_text()
