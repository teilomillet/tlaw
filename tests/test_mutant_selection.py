"""Independent observations of exact selection and its evidence boundaries."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import tlaw.mutation as mutation
from tlaw.audit import ManifestError
from tlaw.cli import main
from tlaw.mutation import KillKind, MutationOutcome, mutate


SOURCE = "def value():\n    return 1\n\ndef other():\n    return 1\n"
LAWS = """from pathlib import Path

import tlaw
from implementation import value

@tlaw.law("observes value", flag=bool)
def observes_value(flag):
    Path("law-marker").write_text("isolated")
    return value() == 1
"""


def _boundary(directory: Path) -> tuple[Path, Path, tuple[str, ...]]:
    laws = directory / "laws.py"
    implementation = directory / "implementation.py"
    laws.write_text(LAWS, encoding="utf-8")
    implementation.write_text(SOURCE, encoding="utf-8")
    candidates = mutation.generate_mutations(SOURCE, "implementation")
    assert [candidate.qualname for candidate in candidates] == ["value", "other"]
    return laws, implementation, tuple(item.mutation_id for item in candidates)


def _observe_children(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout_mutants: bool = False,
    replay_requests: list[dict] | None = None,
) -> list[tuple[str, Path, float]]:
    run_child = mutation.subprocess.run
    calls: list[tuple[str, Path, float]] = []

    def child(command, **kwargs):
        directory = Path(kwargs["cwd"])
        source = (directory / "implementation.py").read_text()
        if kwargs["env"].get("TLAW_REPLAY"):
            phase = "replay"
            if replay_requests is not None:
                replay_requests.append(
                    json.loads(Path(kwargs["env"]["TLAW_REPLAY"]).read_text())
                )
        elif "tlaw canary" in source:
            phase = "canary"
        elif source == SOURCE:
            phase = "baseline"
        else:
            phase = "mutant"
        calls.append((phase, directory, kwargs["timeout"]))
        if timeout_mutants and phase == "mutant":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return run_child(command, **kwargs)

    monkeypatch.setattr(mutation.subprocess, "run", child)
    return calls


@pytest.mark.parametrize(
    "selector", [[], (), "id", b"id", [None], [1], [""], {"id"}, {"id": True}]
)
def test_malformed_selection_starts_no_child_or_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: object
) -> None:
    laws, implementation, _ = _boundary(tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("invalid selection must be rejected before copying or execution")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected)
    monkeypatch.setattr(mutation.shutil, "copytree", unexpected)

    with pytest.raises(ManifestError):
        mutate(laws, implementation, mutant_ids=selector)


@pytest.mark.parametrize("kind", ["unknown", "prefix", "mixed", "stale"])
def test_selection_requires_every_exact_current_id_before_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    requested = {
        "unknown": ["absent-id"],
        "prefix": [ids[0][:8]],
        "mixed": [ids[0], "absent-id"],
        "stale": [ids[0]],
    }[kind]
    if kind == "stale":
        implementation.write_text(SOURCE.replace("return 1", "return 3"))

    def unexpected(*args, **kwargs):
        pytest.fail("unknown or stale IDs must be rejected before any child or copy")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected)
    monkeypatch.setattr(mutation.shutil, "copytree", unexpected)

    with pytest.raises(ManifestError):
        mutate(laws, implementation, mutant_ids=requested)


def test_duplicate_reverse_requests_execute_once_in_generation_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)

    result = mutate(
        laws, implementation, mutant_ids=[ids[1], ids[0], ids[1]], exhaustive=True
    )

    assert [run.mutation.mutation_id for run in result.runs] == list(ids)
    assert [phase for phase, _, _ in calls] == [
        "baseline",
        "canary",
        "mutant",
        "replay",
        "mutant",
    ]
    assert [run.outcome for run in result.runs] == [
        MutationOutcome.KILLED,
        MutationOutcome.SURVIVED,
    ]
    assert result.selected_count == 2
    assert result.excluded_count == result.not_run_count == 0
    assert result.selection_complete and result.complete


def test_selected_kill_uses_fresh_isolation_canary_and_exact_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    replay_requests: list[dict] = []
    calls = _observe_children(monkeypatch, replay_requests=replay_requests)

    result = mutate(laws, implementation, mutant_ids=[ids[0]], exhaustive=True)

    assert [phase for phase, _, _ in calls] == [
        "baseline",
        "canary",
        "mutant",
        "replay",
    ]
    directories = [directory for _, directory, _ in calls]
    assert len(set(directories)) == 4
    assert all(not directory.exists() for directory in directories)
    assert not (tmp_path / "law-marker").exists()
    assert implementation.read_text() == SOURCE
    assert result.canary is not None and result.canary.wired
    selected, excluded = result.runs
    assert selected.outcome is MutationOutcome.KILLED
    assert selected.kill_kind is KillKind.OBSERVED
    assert selected.replay_method == "exact"
    assert replay_requests == [selected.evidence]
    assert selected.evidence["failures"][0]["inputs"] == {
        "flag": {"type": "bool", "value": False}
    }
    assert selected.replay_exit_code == 0
    [replayed] = selected.replay_evidence["obligations"]
    assert replayed["method"] == "replay" and replayed["status"] == "passed"
    assert replayed["cases_checked"] == replayed["cases_total"] == 1
    assert selected.failed_tests
    assert excluded.outcome is MutationOutcome.EXCLUDED
    assert excluded.exit_code is None
    assert excluded.evidence == excluded.replay_evidence == {}
    assert excluded.replay_method is None
    assert result.killed_count == result.runnable_total == result.selected_count == 1
    assert result.excluded_count == 1
    assert result.not_run_count == result.error_count == 0
    assert result.selection_complete and not result.complete
    assert not result.has_errors
    report = result.to_dict()
    assert [row["selected"] for row in report["mutations"]] == [True, False]
    assert report["summary"]["selected"] == report["summary"]["excluded"] == 1
    assert report["summary"]["selection_complete"] is True
    assert report["summary"]["complete"] is False
    assert "selected" in result.statement.lower()
    assert "excluded" in result.render_text().lower()


def test_plan_counts_only_selection_without_compiling_excluded_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    first, second = mutation.generate_mutations(SOURCE, "implementation")
    invalid_excluded = replace(second, mutated_source="def broken(")
    monkeypatch.setattr(
        mutation, "generate_mutations", lambda *args: (first, invalid_excluded)
    )
    compile_source = compile
    compiled: list[str] = []

    def compile_mutant(source, filename, mode):
        compiled.append(filename)
        return compile_source(source, filename, mode)

    monkeypatch.setattr(mutation, "compile", compile_mutant, raising=False)
    calls = _observe_children(monkeypatch)

    result = mutate(
        laws, implementation, mutant_ids=[ids[0]], exhaustive=True, plan_only=True
    )

    assert [phase for phase, _, _ in calls] == ["baseline"]
    assert len(compiled) == 1 and ids[0] in compiled[0]
    assert result.plan is not None
    assert result.plan.generated_mutations == 2
    assert result.plan.selected_mutations == result.plan.excluded_mutations == 1
    assert result.plan.runnable_mutations == 1
    assert (result.plan.min_runs, result.plan.max_runs) == (3, 5)
    assert result.plan.cases_per_run == 2
    assert result.plan.max_case_attempts == 10
    assert [run.outcome for run in result.runs] == [
        MutationOutcome.NOT_RUN,
        MutationOutcome.EXCLUDED,
    ]
    assert result.not_run_count == 1 and result.invalid_count == 0
    assert not result.selection_complete and not result.complete
    assert not result.has_errors


def test_selected_syntax_invalid_candidate_is_invalid_without_mutant_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    first, second = mutation.generate_mutations(SOURCE, "implementation")
    invalid_selected = replace(first, mutated_source="def broken(")
    monkeypatch.setattr(
        mutation, "generate_mutations", lambda *args: (invalid_selected, second)
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, mutant_ids=[ids[0]], exhaustive=True)

    assert [phase for phase, _, _ in calls] == ["baseline", "canary"]
    assert [run.outcome for run in result.runs] == [
        MutationOutcome.INVALID,
        MutationOutcome.EXCLUDED,
    ]
    assert all(run.exit_code is None and run.evidence == {} for run in result.runs)
    assert result.invalid_count == result.selected_count == result.excluded_count == 1
    assert result.killed_count == result.runnable_total == result.not_run_count == 0
    assert result.plan is not None
    assert result.plan.runnable_mutations == 0
    assert result.plan.min_runs == result.plan.max_runs == result.runs_started == 2
    assert result.selection_complete and not result.complete
    assert not result.has_errors


@pytest.mark.parametrize("max_runs", [2, 3])
def test_budget_stop_keeps_excluded_distinct_from_unrun_and_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_runs: int
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    calls = _observe_children(monkeypatch)

    result = mutate(
        laws, implementation, mutant_ids=[ids[0]], exhaustive=True, max_runs=max_runs
    )

    assert len(calls) == max_runs
    assert result.runs[0].outcome is (
        MutationOutcome.NOT_RUN if max_runs == 2 else MutationOutcome.ERROR
    )
    assert result.runs[1].outcome is MutationOutcome.EXCLUDED
    assert result.selected_count == result.excluded_count == 1
    assert result.not_run_count == (1 if max_runs == 2 else 0)
    assert result.killed_count == result.runnable_total == 0
    assert result.has_errors
    assert not result.selection_complete and not result.complete
    assert ids[0] in result.stopped_before


def test_selected_timeout_remeasures_baseline_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    calls = _observe_children(monkeypatch, timeout_mutants=True)

    result = mutate(
        laws,
        implementation,
        mutant_ids=[ids[0]],
        exhaustive=True,
        max_runs=5,
        timeout_floor_seconds=0.1,
        timeout_multiplier=2,
        baseline_timeout_seconds=30,
    )

    assert [phase for phase, _, _ in calls] == [
        "baseline",
        "canary",
        "mutant",
        "baseline",
        "mutant",
    ]
    assert calls[0][2] == calls[3][2] == 30
    assert calls[2][2] >= 0.1 and calls[4][2] >= 0.1
    selected, excluded = result.runs
    assert selected.outcome is MutationOutcome.KILLED
    assert selected.kill_kind is KillKind.TIMEOUT
    assert selected.replay_method == "timeout"
    assert selected.replay_timed_out is True
    assert selected.failed_tests == ()
    assert excluded.outcome is MutationOutcome.EXCLUDED
    assert result.runs_started == 5
    assert result.selection_complete and not result.complete


def test_excluded_equivalence_is_unverified_and_absent_entry_is_stale(
    tmp_path: Path,
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    ledger = tmp_path / "equivalents.toml"
    ledger.write_text(
        "version = 1\n\n"
        f'[equivalent.{ids[1]}]\nreason = "unobserved helper"\n\n'
        '[equivalent.absent]\nreason = "old candidate"\n',
        encoding="utf-8",
    )

    result = mutate(
        laws, implementation, mutant_ids=[ids[0]], exhaustive=True, equivalents=ledger
    )

    assert result.runs[1].outcome is MutationOutcome.EXCLUDED
    assert result.unresolved_equivalent_count == 0
    assert result.stale_ledger_entries == (
        ("absent", "no generated mutation has this ID"),
    )


def test_cli_repeated_exact_ids_report_success_for_selected_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    path = tmp_path / "selected.json"

    exit_code = main(
        [
            "mutate",
            str(laws),
            str(implementation),
            "--exhaustive",
            "--mutant",
            ids[0],
            "--mutant",
            ids[0],
            "--json",
            str(path),
        ]
    )

    assert exit_code == 0
    report = json.loads(path.read_text())
    summary = report["summary"]
    assert summary["selected"] == summary["excluded"] == summary["killed"] == 1
    assert summary["not_run"] == 0
    assert summary["selection_complete"] is True
    assert summary["complete"] is False
    text = capsys.readouterr().out.lower()
    assert "selected" in text and "excluded" in text


def test_cli_unknown_id_starts_no_child_and_writes_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    report = tmp_path / "selected.json"

    def unexpected(*args, **kwargs):
        pytest.fail("CLI must reject an unknown ID before child execution")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected)

    exit_code = main(
        [
            "mutate",
            str(laws),
            str(implementation),
            "--mutant",
            ids[0],
            "--mutant",
            "absent-id",
            "--json",
            str(report),
        ]
    )

    assert exit_code == 2
    assert not report.exists()
    assert "absent-id" in capsys.readouterr().err


def test_cli_can_select_a_later_survivor_without_rerunning_earlier_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    report = tmp_path / "remaining.json"
    calls = _observe_children(monkeypatch)

    exit_code = main(
        [
            "mutate",
            str(laws),
            str(implementation),
            "--exhaustive",
            "--mutant",
            ids[1],
            "--json",
            str(report),
        ]
    )

    assert exit_code == 1
    assert [phase for phase, _, _ in calls] == ["baseline", "canary", "mutant"]
    saved = json.loads(report.read_text())
    assert [run["id"] for run in saved["mutations"]] == list(ids)
    assert [run["outcome"] for run in saved["mutations"]] == ["excluded", "survived"]
    assert saved["summary"]["survived"] == saved["summary"]["runnable"] == 1
    assert saved["summary"]["selection_complete"] is True
    assert saved["summary"]["complete"] is False
    assert "selected" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("failure", ["baseline", "canary"])
def test_selection_cannot_bypass_invalid_baseline_or_surviving_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    laws, implementation, ids = _boundary(tmp_path)
    laws.write_text(
        LAWS.replace("value() == 1", "False" if failure == "baseline" else "True")
    )
    calls = _observe_children(monkeypatch)

    result = mutate(laws, implementation, mutant_ids=[ids[1]], exhaustive=True)

    assert [phase for phase, _, _ in calls] == (
        ["baseline"] if failure == "baseline" else ["baseline", "canary"]
    )
    assert [run.outcome for run in result.runs] == [
        MutationOutcome.EXCLUDED,
        MutationOutcome.NOT_RUN,
    ]
    assert result.killed_count == result.runnable_total == 0
    assert result.excluded_count == result.not_run_count == 1
    assert result.has_errors
    assert not result.selection_complete and not result.complete
