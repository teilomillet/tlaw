"""Laws for the pure mutation-evidence core: no subprocesses here.

These properties pin the three encoded evidence rules where they are
cheapest to falsify: outcome classification, content-based mutation
identity, ledger honesty, and the narrow report statement.
"""

from __future__ import annotations

import ast
import textwrap

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tlaw.audit import ManifestError
from tlaw.mutation import (
    BaselineRun,
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
    outcome, note = classify_mutant_exit(exit_code, timed_out=timed_out)

    if timed_out:
        # An infinite loop is behavior, not infrastructure: the budget is
        # always calibrated against the measured baseline.
        assert outcome is MutationOutcome.KILLED
    elif exit_code == 0:
        assert outcome is MutationOutcome.SURVIVED
    elif exit_code == 1:
        # A False comparison and an implementation exception are both
        # behavioral differences the clean baseline did not show.
        assert outcome is MutationOutcome.KILLED
    elif exit_code == 2:
        # The baseline collected these laws, so a collection failure means
        # the mutant could not be loaded: never a kill, never a success.
        assert outcome is MutationOutcome.INVALID
    else:
        assert outcome is MutationOutcome.ERROR
    assert isinstance(note, str) and note


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
