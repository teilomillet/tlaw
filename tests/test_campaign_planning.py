"""Independent observations of planning, campaign limits and unrun work."""

from __future__ import annotations

import json
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

import tlaw.mutation as mutation
from tlaw.cli import main
from tlaw.mutation import KillKind, MutationOutcome, mutate


IMPLEMENTATION = "def value():\n    return 1\n"
LAWS = """
from pathlib import Path

import tlaw
from implementation import value

Request = tlaw.record(flag=bool, rows=tlaw.list_of(bool, max_size=1))

@tlaw.law("observes value", request=Request)
def observes_value(request):
    Path("law-marker").write_text("inside isolated copy")
    return value() == 1
"""


def _boundary(
    directory: Path, *, laws: str = LAWS, implementation: str = IMPLEMENTATION
) -> tuple[Path, Path]:
    law_file = directory / "laws.py"
    law_file.write_text(textwrap.dedent(laws), encoding="utf-8")
    target = directory / "implementation.py"
    target.write_text(textwrap.dedent(implementation), encoding="utf-8")
    return law_file, target


def _observe_children(
    monkeypatch: pytest.MonkeyPatch, *, timeout_mutants: bool = False
) -> list[tuple[str, float]]:
    run_child = mutation.subprocess.run
    calls: list[tuple[str, float]] = []

    def child(command, **kwargs):
        source = (Path(kwargs["cwd"]) / "implementation.py").read_text()
        if kwargs["env"].get("TLAW_REPLAY"):
            phase = "replay"
        elif "tlaw canary" in source:
            phase = "canary"
        elif "return 2" in source:
            phase = "mutant"
        else:
            phase = "baseline"
        calls.append((phase, kwargs["timeout"]))
        if timeout_mutants and phase == "mutant":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return run_child(command, **kwargs)

    monkeypatch.setattr(mutation.subprocess, "run", child)
    return calls


def test_plan_measures_one_isolated_baseline_and_leaves_all_mutants_unrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, plan_only=True)

    assert [phase for phase, _ in calls] == ["baseline"]
    assert result.runs_started == 1
    assert result.plan_only is True
    assert result.baseline_valid, result.baseline.output
    assert result.canary is None
    assert not result.complete
    assert not result.has_errors
    assert result.killed_count == result.survived_count == result.runnable_total == 0
    assert result.not_run_count == 1
    [unrun] = result.runs
    assert unrun.outcome is MutationOutcome.NOT_RUN
    assert unrun.exit_code is None
    assert unrun.evidence == {}
    assert unrun.replay_method is None
    plan = result.plan
    assert plan is not None
    assert plan.generated_mutations == plan.runnable_mutations == 1
    assert plan.law_cases == 1
    # Two flags times three rows: [], [False], [True].
    assert plan.cases_per_run == 6
    assert (plan.min_runs, plan.max_runs) == (3, 5)
    assert plan.max_case_attempts == 30
    [baseline] = result.baseline.obligations
    assert baseline.cases_checked == baseline.cases_total == 6
    assert baseline.list_bounds == {"request.rows": {"min_size": 0, "max_size": 1}}
    assert not (tmp_path / "law-marker").exists()
    assert implementation.read_text() == IMPLEMENTATION
    assert "Killed 0 of 0" not in result.statement


def test_sampled_plans_keep_case_counts_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, plan_only=True)

    assert len(calls) == 1
    assert result.plan is not None
    assert result.plan.cases_per_run is None
    assert result.plan.max_case_attempts is None
    assert result.to_dict()["plan"]["cases_per_run"] is None
    assert result.to_dict()["plan"]["max_case_attempts"] is None


@pytest.mark.parametrize("plan_only", [False, True])
def test_known_syntax_invalid_mutants_cost_no_runs_and_are_compiled_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_only: bool
) -> None:
    laws, implementation = _boundary(tmp_path)
    [valid] = mutation.generate_mutations(IMPLEMENTATION, "implementation")
    invalid = replace(valid, mutation_id="invalid-syntax", mutated_source="def broken(")
    monkeypatch.setattr(mutation, "generate_mutations", lambda *args: (invalid, valid))
    calls = _observe_children(monkeypatch)
    compiled: list[str] = []
    compile_source = compile

    def compile_mutant(source, filename, mode):
        compiled.append(filename)
        return compile_source(source, filename, mode)

    monkeypatch.setattr(mutation, "compile", compile_mutant, raising=False)

    result = mutate(
        laws,
        implementation,
        exhaustive=True,
        plan_only=plan_only,
        max_runs=1 if plan_only else 4,
    )

    assert result.plan is not None
    assert result.plan.generated_mutations == 2
    assert result.plan.runnable_mutations == 1
    assert (result.plan.min_runs, result.plan.max_runs) == (3, 5)
    assert len(compiled) == 2
    assert len(calls) == result.runs_started == (1 if plan_only else 4)
    assert result.invalid_count == 1
    assert result.runs[0].outcome is MutationOutcome.INVALID
    assert result.runs[0].exit_code is None
    assert result.not_run_count == (1 if plan_only else 0)
    assert result.complete is (not plan_only)
    assert not result.has_errors


def test_plan_sums_the_independent_case_counts_of_every_law(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        laws="""
import tlaw
from implementation import value

@tlaw.law("boolean inputs", flag=bool)
def boolean_inputs(flag):
    return value() == 1

@tlaw.law("bounded histories", values=tlaw.list_of(bool, max_size=2))
def bounded_histories(values):
    return value() == 1
""",
    )

    result = mutate(laws, implementation, exhaustive=True, plan_only=True)

    assert result.baseline_valid, result.baseline.output
    assert result.plan is not None
    assert result.plan.law_cases == 2
    # Two booleans, plus one empty, two singleton and four doubleton lists.
    assert result.plan.cases_per_run == 2 + 1 + 2 + 4
    assert result.plan.max_case_attempts == 9 * 5


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "4"])
def test_invalid_run_budgets_start_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    laws, implementation = _boundary(tmp_path)

    def unexpected_child(*args, **kwargs):
        pytest.fail("invalid work budgets must be rejected before starting a child")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected_child)

    with pytest.raises(ValueError, match="positive integer"):
        mutate(laws, implementation, max_runs=limit)


@pytest.mark.parametrize(
    ("limit", "expected_phases", "expected_outcome", "pending_phase"),
    [
        (1, ["baseline"], "not-run", "canary"),
        (2, ["baseline", "canary"], "not-run", "mutant"),
        (3, ["baseline", "canary", "mutant"], "error", "replay"),
        (4, ["baseline", "canary", "mutant", "replay"], "killed", None),
    ],
)
def test_run_budget_counts_every_phase_and_never_counts_an_unconfirmed_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    expected_phases: list[str],
    expected_outcome: str,
    pending_phase: str | None,
) -> None:
    laws, implementation = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, max_runs=limit)

    assert [phase for phase, _ in calls] == expected_phases
    assert result.runs_started == len(calls) == limit
    assert calls[0][1] == 60
    assert all(timeout == result.budget_seconds for _, timeout in calls[1:])
    [run] = result.runs
    assert run.outcome.value == expected_outcome
    assert result.not_run_count == (1 if limit < 3 else 0)
    assert result.killed_count == (1 if limit == 4 else 0)
    assert result.complete is (limit == 4)
    assert result.has_errors is (limit != 4)
    work = result.to_dict()["work"]
    assert work["max_runs"] == limit
    assert work["runs_started"] == len(calls)
    assert work["runs_remaining"] == 0
    assert result.to_dict()["summary"]["complete"] is (limit == 4)
    assert result.to_dict()["summary"]["not_run"] == result.not_run_count
    if pending_phase is None:
        assert result.stopped_before is None
        assert run.kill_kind is KillKind.OBSERVED
        assert run.replay_method == "exact"
        assert run.replay_exit_code == 0
        [replayed] = run.replay_evidence["obligations"]
        assert replayed["method"] == "replay"
        assert replayed["cases_checked"] == replayed["cases_total"] == 1
    else:
        assert pending_phase in result.stopped_before
        assert work["stopped_before"] == result.stopped_before
        if limit > 1:
            assert run.mutation.mutation_id in result.stopped_before
        assert run.kill_kind is None
        assert run.failed_tests == ()
        assert run.replay_method is None
        if limit == 3:
            # The observed counterexample remains history, not a confirmed kill.
            assert run.exit_code == 1
            assert run.evidence["failures"]
            assert "replay" in run.note


def test_a_survivor_needs_no_confirmation_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path, laws=LAWS.replace("value() == 1", "value() in (1, 2)")
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, max_runs=3)

    assert [phase for phase, _ in calls] == ["baseline", "canary", "mutant"]
    assert result.complete
    assert not result.has_errors
    assert result.survived_count == result.runnable_total == 1
    assert result.stopped_before is None
    [survivor] = result.runs
    [receipt] = survivor.evidence["obligations"]
    assert receipt["cases_checked"] == receipt["cases_total"] == 6
    assert receipt["exhausted"] is True


@pytest.mark.parametrize("limit", [1, 3])
def test_a_budget_stop_does_not_copy_another_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    laws, implementation = _boundary(tmp_path)
    copy_boundary = mutation.shutil.copytree
    copies: list[Path] = []

    def copytree(source, destination, *args, **kwargs):
        if Path(source).resolve() == tmp_path.resolve():
            copies.append(Path(destination))
            assert len(copies) <= limit, "budget exhausted before boundary copying"
        return copy_boundary(source, destination, *args, **kwargs)

    monkeypatch.setattr(mutation.shutil, "copytree", copytree)
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, max_runs=limit)

    assert len(copies) == len(calls) == result.runs_started == limit
    assert len(set(copies)) == limit
    assert all(not destination.exists() for destination in copies)


@pytest.mark.parametrize("limit", [3, 4, 5])
def test_timeout_confirmation_counts_fresh_calibration_and_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    laws, implementation = _boundary(tmp_path)
    calls = _observe_children(monkeypatch, timeout_mutants=True)

    result = mutate(
        laws,
        implementation,
        exhaustive=True,
        max_runs=limit,
        timeout_floor_seconds=0.1,
        timeout_multiplier=2,
        baseline_timeout_seconds=30,
    )

    expected = ["baseline", "canary", "mutant", "baseline", "mutant"]
    assert [phase for phase, _ in calls] == expected[:limit]
    assert result.runs_started == len(calls) == limit
    assert calls[0][1] == 30
    if limit >= 4:
        assert calls[3][1] == 30
    [run] = result.runs
    assert run.timed_out
    assert run.failed_tests == ()
    assert result.complete is (limit == 5)
    if limit < 5:
        assert run.outcome is MutationOutcome.ERROR
        assert run.kill_kind is None
        assert result.killed_count == 0
        assert result.has_errors
        assert run.mutation.mutation_id in result.stopped_before
        assert "timeout" in result.stopped_before
        assert ("baseline" if limit == 3 else "retry") in result.stopped_before
    else:
        assert run.outcome is MutationOutcome.KILLED
        assert run.kill_kind is KillKind.TIMEOUT
        assert run.replay_method == "timeout"
        assert run.replay_timed_out is True
        assert result.killed_by_timeout_count == 1
        assert result.stopped_before is None
        assert calls[-1][1] >= 0.1


def test_budget_stop_lists_remaining_mutants_without_honoring_or_refuting_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation_text = "def value():\n    return 1\n\ndef other():\n    return 1\n"
    laws, implementation = _boundary(tmp_path, implementation=implementation_text)
    generated = mutation.generate_mutations(implementation_text, "implementation")
    assert len(generated) == 2
    ledger = tmp_path / "equivalents.toml"
    ledger.write_text(
        "version = 1\n\n"
        f"[equivalent.{generated[1].mutation_id}]\n"
        'reason = "not observed by these laws"\n',
        encoding="utf-8",
    )
    calls = _observe_children(monkeypatch)

    result = mutate(
        laws, implementation, exhaustive=True, max_runs=4, equivalents=ledger
    )

    assert [phase for phase, _ in calls] == ["baseline", "canary", "mutant", "replay"]
    assert [run.mutation.mutation_id for run in result.runs] == [
        candidate.mutation_id for candidate in generated
    ]
    assert result.killed_count == result.runnable_total == 1
    assert result.not_run_count == 1
    assert result.runs[1].outcome is MutationOutcome.NOT_RUN
    assert result.stale_ledger_entries == ()
    assert result.unresolved_equivalent_count == 0
    assert not result.complete
    assert result.plan is not None
    assert (result.plan.min_runs, result.plan.max_runs) == (4, 8)
    assert result.plan.max_case_attempts == 48


@pytest.mark.parametrize("plan_only", [False, True])
def test_cli_distinguishes_requested_plan_from_incomplete_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan_only: bool,
) -> None:
    laws, implementation = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)
    report_path = tmp_path / "campaign.json"
    options = ["--plan"] if plan_only else ["--max-runs", "1"]

    exit_code = main(
        [
            "mutate",
            str(laws),
            str(implementation),
            "--exhaustive",
            *options,
            "--json",
            str(report_path),
        ]
    )

    assert exit_code == (0 if plan_only else 2)
    assert len(calls) == 1
    recorded = json.loads(report_path.read_text())
    assert recorded["plan_only"] is plan_only
    assert recorded["plan"]["cases_per_run"] == 6
    assert recorded["work"]["runs_started"] == 1
    assert recorded["summary"]["complete"] is False
    assert recorded["summary"]["not_run"] == 1
    assert recorded["summary"]["runnable"] == 0
    text = capsys.readouterr().out.lower()
    assert "plan" in text
    assert "unrun" in text or "not run" in text
    assert "6" in text
    if plan_only:
        assert "killed 0 of 0" not in text
    else:
        assert "incomplete" in text


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_invalid_cli_run_budget_starts_no_child_and_writes_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    limit: str,
) -> None:
    laws, implementation = _boundary(tmp_path)
    report_path = tmp_path / "campaign.json"

    def unexpected_child(*args, **kwargs):
        pytest.fail("invalid CLI budget must be rejected before child execution")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected_child)

    assert (
        main(
            [
                "mutate",
                str(laws),
                str(implementation),
                "--max-runs",
                limit,
                "--json",
                str(report_path),
            ]
        )
        == 2
    )
    assert not report_path.exists()
    assert "positive" in capsys.readouterr().err


def test_invalid_baseline_cannot_produce_a_valid_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path, laws=LAWS.replace("value() == 1", "False")
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, plan_only=True)

    assert len(calls) == 1
    assert not result.baseline_valid
    assert result.plan is None
    assert result.has_errors
    assert not result.complete
    assert result.canary is None
    assert result.not_run_count == 1
    assert all(run.outcome is MutationOutcome.NOT_RUN for run in result.runs)


def test_a_surviving_canary_leaves_mutants_unrun_without_claiming_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path, laws=LAWS.replace("value() == 1", "True")
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True)

    assert [phase for phase, _ in calls] == ["baseline", "canary"]
    assert result.canary is not None and result.canary.wired is False
    assert result.plan is not None
    assert result.has_errors
    assert not result.complete
    assert result.not_run_count == 1
    assert result.error_count == 0
    assert result.killed_count == result.runnable_total == 0
    assert result.runs[0].outcome is MutationOutcome.NOT_RUN


def test_zero_mutation_plan_needs_only_a_baseline_and_claims_no_strength(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        implementation="def value():\n    return None\n",
        laws=LAWS.replace("value() == 1", "value() is None"),
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, exhaustive=True, plan_only=True)

    assert len(calls) == 1
    assert result.plan is not None
    assert result.plan.generated_mutations == result.plan.runnable_mutations == 0
    assert (result.plan.min_runs, result.plan.max_runs) == (1, 1)
    assert result.canary is None
    assert result.runs == ()
    assert result.killed_count == result.runnable_total == 0
    assert "no mutation evidence" in result.statement.lower()
