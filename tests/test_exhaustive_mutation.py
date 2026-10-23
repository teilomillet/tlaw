"""Independent challenges to finite mutation evidence and its replay boundary."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

import tlaw.mutation as mutation
from tlaw.cli import main
from tlaw.mutation import KillKind, MutationOutcome, mutate
from tlaw.obligations import OBLIGATIONS_ENV


IMPLEMENTATION = "def value():\n    return 1\n"
NESTED_LAWS = """
import os
from pathlib import Path
import tlaw
from hypothesis import strategies as st
from implementation import value

Request = tlaw.record(
    flag=bool,
    rows=tlaw.list_of(tlaw.record(bit=bool), max_size=1),
)
# A replay must use the retained input, even if generation is impossible.
Inputs = st.nothing() if os.environ.get("TLAW_REPLAY") else Request

@tlaw.law("nested finite input", request=Inputs)
def nested_input(request):
    Path("child-marker").write_text("isolated law execution")
    observed = value()
    return observed == 1 or request != {"flag": False, "rows": [{"bit": True}]}
"""
BOUNDS = {"request.rows": {"min_size": 0, "max_size": 1}}


def _boundary(tmp_path: Path, laws: str = NESTED_LAWS) -> tuple[Path, Path]:
    implementation = tmp_path / "implementation.py"
    implementation.write_text(IMPLEMENTATION, encoding="utf-8")
    law_file = tmp_path / "laws.py"
    law_file.write_text(textwrap.dedent(laws), encoding="utf-8")
    return law_file, implementation


def _phase(kwargs: dict) -> str:
    source = (Path(kwargs["cwd"]) / "implementation.py").read_text()
    if "tlaw canary" in source:
        return "canary"
    return "mutant" if "return 2" in source else "baseline"


def test_nested_finite_mutation_counts_and_retained_replay_are_independent(
    tmp_path: Path,
) -> None:
    laws, implementation = _boundary(tmp_path)

    result = mutate(laws, implementation, exhaustive=True, max_cases=6)

    assert result.baseline_valid, result.baseline.output
    assert result.method == "exhaustive"
    assert result.max_cases == 6
    [baseline] = result.baseline.obligations
    # Two flags times three rows: [], [{bit: False}], [{bit: True}].
    assert baseline.method == "exhaustive"
    assert baseline.cases_checked == baseline.cases_total == 6
    assert baseline.exhausted is True
    assert baseline.list_bounds == BOUNDS
    assert result.canary is not None and result.canary.wired is True
    [canary] = result.canary.obligations
    assert canary.method == "exhaustive"
    assert canary.cases_checked == 1
    assert canary.cases_total == 6
    assert canary.list_bounds == BOUNDS
    assert canary.exhausted is False

    [run] = result.runs
    assert run.outcome is MutationOutcome.KILLED
    assert run.kill_kind is KillKind.OBSERVED
    assert run.evidence["mode"] == "exhaustive"
    [failed] = run.evidence["obligations"]
    assert failed["method"] == "exhaustive"
    assert failed["cases_checked"] == 3
    assert failed["cases_total"] == 6
    assert failed["exhausted"] is False
    assert failed["list_bounds"] == BOUNDS
    assert run.replay_method == "exact"
    assert run.replay_exit_code == 0
    [replayed] = run.replay_evidence["obligations"]
    assert replayed["method"] == "replay"
    assert replayed["cases_checked"] == replayed["cases_total"] == 1
    assert replayed["list_bounds"] == {}
    encoded = result.to_dict()
    assert encoded["method"] == "exhaustive"
    assert encoded["max_cases"] == 6
    assert encoded["mutations"][0]["evidence"] == run.evidence
    text = result.render_text()
    assert "6/6" in text
    assert "3/6" in text
    assert "request.rows=0..1" in text
    assert not (tmp_path / "child-marker").exists()
    assert implementation.read_text() == IMPLEMENTATION


@pytest.mark.parametrize("partial_receipt", [False, True])
def test_a_finite_survivor_must_finish_every_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial_receipt: bool
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        NESTED_LAWS.replace(
            'observed == 1 or request != {"flag": False, "rows": [{"bit": True}]}',
            "observed in (1, 2)",
        ),
    )
    run_child = mutation.subprocess.run

    def child(command, **kwargs):
        completed = run_child(command, **kwargs)
        if partial_receipt and _phase(kwargs) == "mutant":
            destination = Path(kwargs["env"][OBLIGATIONS_ENV])
            receipt = json.loads(destination.read_text())
            receipt[0].update(cases_checked=5, exhausted=False)
            destination.write_text(json.dumps(receipt))
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation, exhaustive=True, max_cases=6)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    [run] = result.runs
    if partial_receipt:
        assert run.exit_code == 0
        assert run.outcome is MutationOutcome.ERROR
        assert result.survived_count == 0
        assert "incomplete law obligations" in run.note
        return
    assert run.outcome is MutationOutcome.SURVIVED
    [obligation] = run.evidence["obligations"]
    assert obligation["method"] == "exhaustive"
    assert obligation["cases_checked"] == obligation["cases_total"] == 6
    assert obligation["exhausted"] is True
    assert obligation["list_bounds"] == BOUNDS
    assert run.replay_method is None


@pytest.mark.parametrize("exhaustive", [False, True])
def test_default_mode_and_case_limit_are_explicit(
    tmp_path: Path, exhaustive: bool
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        """
import tlaw
from implementation import value

@tlaw.law
def value_is_one(flag: bool):
    return value() == 1
""",
    )

    result = (
        mutate(laws, implementation, exhaustive=True)
        if exhaustive
        else mutate(laws, implementation)
    )

    expected_method = "exhaustive" if exhaustive else "sampled"
    expected_limit = 1000 if exhaustive else None
    assert result.killed_count == 1
    assert result.method == expected_method
    assert result.max_cases == expected_limit
    encoded = result.to_dict()
    assert encoded["method"] == expected_method
    assert encoded["max_cases"] == expected_limit
    assert expected_method in result.render_text()
    [baseline] = result.baseline.obligations
    assert baseline.method == expected_method
    if exhaustive:
        assert baseline.cases_checked == baseline.cases_total == 2
        assert baseline.exhausted is True
    else:
        assert baseline.cases_checked is baseline.cases_total is None
        assert "total input space not enumerated" in result.render_text()


@pytest.mark.parametrize(
    ("declaration", "limit"),
    [("tlaw.integer(0, 6)", 6), ("tlaw.list_of(bool)", 1000)],
)
def test_unsupported_or_oversized_domains_do_not_fall_back_to_sampling(
    tmp_path: Path, declaration: str, limit: int
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        f"""
import tlaw
from implementation import value

Inputs = {declaration}

@tlaw.law("unsupported finite declaration", item=Inputs)
def observes_boundary(item):
    return value() == 1
""",
    )

    result = mutate(laws, implementation, exhaustive=True, max_cases=limit)

    assert result.method == "exhaustive"
    assert result.max_cases == limit
    assert not result.baseline_valid
    assert result.canary is None
    assert result.has_errors
    assert result.killed_count == result.survived_count == 0
    assert all(run.outcome is MutationOutcome.NOT_RUN for run in result.runs)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "6"])
def test_exhaustive_case_limit_requires_a_positive_integer(
    tmp_path: Path, limit: object
) -> None:
    laws, implementation = _boundary(tmp_path)

    with pytest.raises(ValueError, match="positive integer"):
        mutate(laws, implementation, exhaustive=True, max_cases=limit)


def test_a_case_limit_requires_explicit_exhaustive_mode(tmp_path: Path) -> None:
    laws, implementation = _boundary(tmp_path)

    with pytest.raises(ValueError, match="exhaustive=True"):
        mutate(laws, implementation, max_cases=6)


def test_inherited_environment_cannot_change_selected_mode_or_exact_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(tmp_path)
    foreign = tmp_path / "foreign.json"
    foreign.write_text("must remain untouched")
    for name, value in {
        "TLAW_MODE": "default",
        "TLAW_MAX_CASES": "1",
        "TLAW_SEED": "invalid seed",
        "TLAW_REPLAY": str(foreign),
        "TLAW_REPLAY_ROOT": "/wrong-root",
        "TLAW_CAPTURE": str(foreign),
        "TLAW_OBLIGATIONS": str(foreign),
    }.items():
        monkeypatch.setenv(name, value)
    run_child = mutation.subprocess.run
    seen: list[tuple[str | None, str | None, bool]] = []

    def child(command, **kwargs):
        environment = kwargs["env"]
        seen.append(
            (
                environment.get("TLAW_MODE"),
                environment.get("TLAW_MAX_CASES"),
                bool(environment.get("TLAW_REPLAY")),
            )
        )
        assert "TLAW_SEED" not in environment
        return run_child(command, **kwargs)

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation, exhaustive=True, max_cases=6)

    assert result.killed_count == 1
    assert seen[:3] == [("exhaustive", "6", False)] * 3
    assert len(seen) == 4
    assert seen[-1][1:] == (None, True)
    assert foreign.read_text() == "must remain untouched"


@pytest.mark.parametrize("phase", ["canary", "mutant"])
@pytest.mark.parametrize("alteration", ["method", "total", "bounds"])
def test_changed_finite_receipts_cannot_support_mutation_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    alteration: str,
) -> None:
    laws, implementation = _boundary(tmp_path)
    run_child = mutation.subprocess.run

    def child(command, **kwargs):
        completed = run_child(command, **kwargs)
        if _phase(kwargs) == phase and not kwargs["env"].get("TLAW_REPLAY"):
            destination = Path(kwargs["env"][OBLIGATIONS_ENV])
            receipt = json.loads(destination.read_text())
            [obligation] = receipt
            if alteration == "method":
                obligation.update(
                    method="sampled",
                    cases_checked=None,
                    cases_total=None,
                    exhausted=False,
                    list_bounds={},
                )
            elif alteration == "total":
                obligation["cases_total"] += 1
            else:
                obligation["list_bounds"]["request.rows"]["max_size"] = 2
            destination.write_text(json.dumps(receipt))
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation, exhaustive=True, max_cases=10)

    assert result.baseline_valid
    if phase == "canary":
        assert result.canary is not None and result.canary.wired is None
        assert result.canary.evidence_error
    else:
        assert result.canary is not None and result.canary.wired is True
        assert result.runs[0].replay_method is None
    assert result.has_errors
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("alteration", ["method", "cap"])
def test_baseline_must_report_the_requested_finite_mode_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alteration: str
) -> None:
    laws, implementation = _boundary(tmp_path)
    run_child = mutation.subprocess.run

    def child(command, **kwargs):
        completed = run_child(command, **kwargs)
        destination = Path(kwargs["env"][OBLIGATIONS_ENV])
        receipt = json.loads(destination.read_text())
        [obligation] = receipt
        if alteration == "method":
            obligation.update(
                method="sampled",
                cases_checked=None,
                cases_total=None,
                exhausted=False,
                list_bounds={},
            )
        else:
            obligation["cases_checked"] = obligation["cases_total"] = 7
        destination.write_text(json.dumps(receipt))
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation, exhaustive=True, max_cases=6)

    assert not result.baseline_valid
    assert result.baseline.evidence_error
    assert result.canary is None
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("changed_fresh_receipt", [False, True])
def test_timeout_confirmation_repeats_the_same_finite_mode_and_case_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_fresh_receipt: bool,
) -> None:
    laws, implementation = _boundary(tmp_path)
    run_child = mutation.subprocess.run
    seen: list[tuple[str, str | None, str | None, bool, float]] = []

    def child(command, **kwargs):
        phase = _phase(kwargs)
        environment = kwargs["env"]
        seen.append(
            (
                phase,
                environment.get("TLAW_MODE"),
                environment.get("TLAW_MAX_CASES"),
                bool(environment.get("TLAW_REPLAY")),
                kwargs["timeout"],
            )
        )
        if phase == "mutant":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        completed = run_child(command, **kwargs)
        if changed_fresh_receipt and phase == "baseline" and len(seen) > 1:
            destination = Path(environment[OBLIGATIONS_ENV])
            receipt = json.loads(destination.read_text())
            receipt[0]["list_bounds"]["request.rows"]["max_size"] = 2
            destination.write_text(json.dumps(receipt))
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(
        laws,
        implementation,
        exhaustive=True,
        max_cases=6,
        timeout_floor_seconds=0.1,
        timeout_multiplier=2.0,
    )

    assert result.baseline_valid
    phases = ["baseline", "canary", "mutant", "baseline"]
    if not changed_fresh_receipt:
        phases.append("mutant")
    assert [call[0] for call in seen] == phases
    assert all(call[1:4] == ("exhaustive", "6", False) for call in seen)
    [run] = result.runs
    if changed_fresh_receipt:
        assert run.outcome is MutationOutcome.ERROR
        assert run.kill_kind is None
        assert run.failed_tests == ()
        assert "re-measured baseline no longer passes" in run.note
        return
    assert run.outcome is MutationOutcome.KILLED
    assert run.kill_kind is KillKind.TIMEOUT
    assert run.replay_method == "timeout"
    assert run.failed_tests == ()
    assert "re-measured" in run.note
    assert seen[-1][-1] >= 0.1


def test_cli_reports_finite_mode_bounds_and_counts_in_text_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    laws, implementation = _boundary(tmp_path)
    report_path = tmp_path / "mutation.json"

    assert (
        main(
            [
                "mutate",
                str(laws),
                str(implementation),
                "--exhaustive",
                "--max-cases",
                "6",
                "--json",
                str(report_path),
            ]
        )
        == 0
    )

    report = json.loads(report_path.read_text())
    assert report["method"] == "exhaustive"
    assert report["max_cases"] == 6
    assert report["baseline"]["obligations"][0]["cases_total"] == 6
    assert report["mutations"][0]["evidence"]["obligations"][0]["cases_checked"] == 3
    text = capsys.readouterr().out
    assert "exhaustive" in text
    assert "6/6" in text
    assert "3/6" in text
    assert "request.rows=0..1" in text


@pytest.mark.parametrize(
    "options",
    [
        ["--max-cases", "6"],
        ["--exhaustive", "--max-cases", "0"],
        ["--exhaustive", "--max-cases", "-1"],
    ],
)
def test_invalid_cli_case_limits_start_no_child_and_write_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
) -> None:
    laws, implementation = _boundary(tmp_path)
    report_path = tmp_path / "mutation.json"

    def unexpected_child(*args, **kwargs):
        pytest.fail("invalid CLI options must be rejected before starting a child")

    monkeypatch.setattr(mutation.subprocess, "run", unexpected_child)

    assert (
        main(
            [
                "mutate",
                str(laws),
                str(implementation),
                *options,
                "--json",
                str(report_path),
            ]
        )
        == 2
    )

    assert not report_path.exists()
    assert "positive integer" in capsys.readouterr().err
