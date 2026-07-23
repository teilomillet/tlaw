from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest
import tlaw

from tlaw.cli import main


FAILING_SEARCH_LAW = """\
import tlaw


@tlaw.law
def integers_stay_below_ten(value: int):
    return tlaw.same(value, min(value, 9))
"""


def test_search_records_its_seed_shrunk_inputs_and_owned_comparison(
    tmp_path: Path,
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(FAILING_SEARCH_LAW, encoding="utf-8")

    result = tlaw.check(law_file, search=True, seed=20260723)
    report = json.loads(result.to_json())

    assert result.exit_code == 1
    assert "tlaw search seed: 20260723" in result.output
    assert report["schema"] == "tlaw.check/v1"
    assert report["mode"] == "search"
    assert report["seed"] == 20260723
    assert report["summary"] == {"failures": 1, "status": "failed"}
    [failure] = report["failures"]
    assert failure["identity"].endswith("laws.py::integers_stay_below_ten")
    assert failure["generation"] == "search-seeded-inferred"
    assert failure["inputs"] == {"value": {"type": "int", "value": "10"}}
    assert failure["comparison"]["left"] == "10"
    assert failure["comparison"]["right"] == "9"
    assert failure["comparison"]["within"] == 0.0
    assert failure["comparison"]["relative"] == 0.0

    repeated = json.loads(
        tlaw.check(law_file, search=True, seed=report["seed"]).to_json()
    )
    assert repeated["failures"][0]["inputs"] == failure["inputs"]


def test_replay_runs_only_the_retained_counterexample_and_succeeds_when_it_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(FAILING_SEARCH_LAW, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    report_path = tmp_path / "failure.json"

    assert (
        main(
            [
                "check",
                "laws.py",
                "--search",
                "--json",
                str(report_path),
            ]
        )
        == 1
    )
    search_output = capsys.readouterr().out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert f"tlaw search seed: {report['seed']}" in search_output
    assert f"Replay: tlaw check --replay {report_path}" in search_output

    law_file.write_text(
        law_file.read_text(encoding="utf-8")
        + "\n\ndef test_unrelated_failure():\n    assert False\n",
        encoding="utf-8",
    )
    assert main(["check", "--replay", str(report_path)]) == 0
    replay_output = capsys.readouterr().out
    assert "tlaw replay: 1 retained counterexample reproduced" in replay_output
    assert "1 passed" in replay_output

    law_file.write_text(
        """\
import tlaw


@tlaw.law
def integers_stay_below_ten(value: int):
    return True
""",
        encoding="utf-8",
    )
    assert main(["check", "--replay", str(report_path)]) == 1
    changed_output = capsys.readouterr().out
    assert "retained counterexample no longer fails" in changed_output


def test_replay_is_invalid_when_any_retained_law_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def first_law(value: int):
    return False


@tlaw.law
def second_law(value: int):
    return False
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = tlaw.check("laws.py", search=True, seed=9)
    assert len(result.report["failures"]) == 2
    report = tmp_path / "two-failures.json"
    report.write_text(result.to_json(), encoding="utf-8")

    law_file.write_text(
        """\
import tlaw


@tlaw.law
def first_law(value: int):
    return False
""",
        encoding="utf-8",
    )

    assert main(["check", "--replay", str(report)]) == 2
    assert "could not find retained laws" in capsys.readouterr().out


def test_one_law_can_retain_and_replay_distinct_failure_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


Value = tlaw.one_of([-1, 1])


@tlaw.law
def both_sides_are_invalid(value: Value):
    if value < 0:
        raise ValueError("negative branch")
    raise TypeError("positive branch")
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = tlaw.check("laws.py", search=True, seed=12)

    assert result.exit_code == 1
    assert len(result.report["failures"]) == 2
    assert {failure["exception"]["type"] for failure in result.report["failures"]} == {
        "builtins.TypeError",
        "builtins.ValueError",
    }
    report = tmp_path / "distinct-failures.json"
    report.write_text(result.to_json(), encoding="utf-8")
    assert tlaw.replay(report).exit_code == 0


def test_replay_retains_the_input_before_a_law_mutates_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


Values = tlaw.list_of(tlaw.integer(0, 1), min_size=1, max_size=3)


@tlaw.law
def clearing_an_input_is_not_a_proof(values: Values):
    values.clear()
    return True
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = tlaw.check("laws.py", search=True, seed=23)

    assert result.exit_code == 1
    [failure] = result.report["failures"]
    assert failure["inputs"] == {
        "values": {
            "type": "list",
            "items": [{"type": "int", "value": "0"}],
        }
    }
    report = tmp_path / "mutated-input.json"
    report.write_text(result.to_json(), encoding="utf-8")
    assert tlaw.replay(report).exit_code == 0


def test_relative_report_replays_from_an_independent_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first-checkout"
    first.mkdir()
    law_file = first / "laws.py"
    law_file.write_text(FAILING_SEARCH_LAW, encoding="utf-8")
    monkeypatch.chdir(first)
    report = first / "failure.json"
    report.write_text(
        tlaw.check("laws.py", search=True, seed=51).to_json(),
        encoding="utf-8",
    )

    second = tmp_path / "second-checkout"
    second.mkdir()
    shutil.copy2(law_file, second / "laws.py")
    shutil.copy2(report, second / "failure.json")
    monkeypatch.chdir(second)

    replayed = tlaw.replay("failure.json")

    assert replayed.exit_code == 0
    assert "retained counterexample reproduced" in replayed.output


def test_search_seed_is_new_by_default_and_visible_in_explain_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def zero_times_anything_is_zero(value: int):
    return value * 0 == 0
""",
        encoding="utf-8",
    )
    check_module = importlib.import_module("tlaw.check")
    monkeypatch.setattr(check_module.secrets, "randbits", lambda bits: 424242)

    result = tlaw.check(law_file, search=True, explain=True)

    assert result.exit_code == 0
    assert "tlaw search seed: 424242" in result.output
    assert "randomized search, seed 424242" in result.output
    report = json.loads(result.to_json())
    assert report["seed"] == 424242
    assert report["summary"] == {"failures": 0, "status": "passed"}


def test_search_explains_the_explicit_forms_real_posture(tmp_path: Path) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
from hypothesis import strategies as st

import tlaw


@tlaw.law("explicit failure", value=st.integers())
def explicit_failure(value):
    return False
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file, search=True, seed=88, explain=True)

    assert result.exit_code == 1
    assert "explicit strategies; search seed 88" in result.output
    assert "Hypothesis settings decide examples and phases" in result.output
    assert result.report["failures"][0]["generation"] == (
        "search-seeded-explicit-settings"
    )


def test_unserializable_counterexample_is_invalid_evidence_not_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def complex_values_are_real(value: complex):
    return False
""",
        encoding="utf-8",
    )

    result = tlaw.check(law_file, search=True, seed=7)
    report = json.loads(result.to_json())

    assert result.exit_code == 2
    assert report["summary"]["status"] == "invalid"
    assert "cannot retain input 'value' of type complex" in result.output

    retained = tmp_path / "invalid.json"
    assert (
        main(
            [
                "check",
                str(law_file),
                "--search",
                "--seed",
                "7",
                "--json",
                str(retained),
            ]
        )
        == 2
    )
    assert not retained.exists()
    assert "JSON not written" in capsys.readouterr().err


def test_check_reports_a_json_write_error_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        """\
import tlaw


@tlaw.law
def identity(value: int):
    return value == value
""",
        encoding="utf-8",
    )
    destination = tmp_path / "missing" / "report.json"

    assert main(["check", str(law_file), "--json", str(destination)]) == 2
    assert not destination.exists()
    assert "could not write JSON report" in capsys.readouterr().err


def test_replay_refuses_malformed_or_incomplete_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"schema": "something-else"}', encoding="utf-8")

    assert main(["check", "--replay", str(malformed)]) == 2
    assert "tlaw:" in capsys.readouterr().err

    empty = tmp_path / "empty.json"
    empty.write_text(
        json.dumps(
            {
                "schema": "tlaw.check/v1",
                "law_file": "laws.py",
                "mode": "search",
                "seed": 1,
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    assert main(["check", "--replay", str(empty)]) == 2
    assert "no retained failures" in capsys.readouterr().err

    law_file = tmp_path / "laws.py"
    law_file.write_text(FAILING_SEARCH_LAW, encoding="utf-8")
    assert main(["check", str(law_file), "--seed", "1"]) == 2
    assert "requires --search" in capsys.readouterr().err
    assert main(["check", str(law_file), "--replay", str(malformed)]) == 2
    assert "do not also pass a law file" in capsys.readouterr().err
