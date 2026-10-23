"""Public evidence for complete bounded histories, separate from sampled laws."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest
import tlaw

from tlaw.cli import main
from tlaw.mutation import mutate


def _write_laws(tmp_path: Path, declarations: str, law: str) -> Path:
    source = """\
import json
from pathlib import Path
import tlaw

def record(value):
    with Path('calls.jsonl').open('a') as calls:
        calls.write(json.dumps(value) + '\\n')

"""
    path = tmp_path / "laws.py"
    path.write_text(source + declarations + "\n\n" + law, encoding="utf-8")
    return path


def _calls(tmp_path: Path) -> list:
    return [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]


def _histories(alphabet: list, minimum: int, maximum: int) -> list[list]:
    return [
        list(items)
        for size in range(minimum, maximum + 1)
        for items in itertools.product(alphabet, repeat=size)
    ]


def _passing_law(parameter: str = "moves: Moves") -> str:
    return (
        f"@tlaw.law\ndef histories({parameter}):\n    record(moves)\n    return True\n"
    )


def test_cli_checks_all_31_histories_and_reports_the_length_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Move = tlaw.one_of(['left', 'right'])\nMoves = tlaw.list_of(Move, max_size=4)",
        _passing_law(),
    )
    report_path = tmp_path / "report.json"

    assert (
        main(["check", str(law_file), "--exhaustive", "--json", str(report_path)]) == 0
    )

    assert _calls(tmp_path) == _histories(["left", "right"], 0, 4)
    report = json.loads(report_path.read_text())
    [obligation] = report["obligations"]
    assert obligation["method"] == "exhaustive"
    assert obligation["cases_checked"] == obligation["cases_total"] == 31
    assert obligation["exhausted"] is True
    assert obligation["list_bounds"] == {"moves": {"min_size": 0, "max_size": 4}}
    output = capsys.readouterr().out
    assert "31/31" in output
    assert "lengths: moves=0..4" in output


def test_folder_cli_reports_bounds_and_partial_counts_for_each_law(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(bool, max_size=2)",
        "@tlaw.law\ndef passes(moves: Moves):\n    return True\n\n"
        "@tlaw.law\ndef fails(moves: Moves):\n    return len(moves) < 1\n",
    )

    assert main(["check", str(tmp_path), "--exhaustive"]) == 1

    output = capsys.readouterr().out
    assert "7/7 cases checked; lengths: moves=0..2; all declared cases passed" in output
    assert "2/7 cases checked; lengths: moves=0..2; not exhausted" in output


@pytest.mark.parametrize(
    "alphabet, minimum, maximum, expected",
    [
        ([False, True], 0, 0, [[]]),
        (["only"], 0, 3, [[], ["only"], ["only", "only"], ["only", "only", "only"]]),
        ([False, True], 2, 3, _histories([False, True], 2, 3)),
        ([False, 0, False], 1, 1, [[False], [0], [False]]),
    ],
)
def test_list_cases_preserve_the_declared_lengths_entries_and_order(
    tmp_path: Path, alphabet: list, minimum: int, maximum: int, expected: list
) -> None:
    law_file = _write_laws(
        tmp_path,
        f"Moves = tlaw.list_of(tlaw.one_of({alphabet!r}), min_size={minimum}, max_size={maximum})",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == expected
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == len(expected)
    assert obligation["list_bounds"] == {
        "moves": {"min_size": minimum, "max_size": maximum}
    }


def test_list_elements_keep_type_distinct_and_duplicate_declaration_entries(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.one_of([False, 0, False]), min_size=1, max_size=2)",
        "@tlaw.law\ndef histories(moves: Moves):\n"
        "    record([[type(move).__name__, move] for move in moves])\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=12)

    assert result.exit_code == 0, result.output
    alphabet = [["bool", False], ["int", 0], ["bool", False]]
    assert _calls(tmp_path) == _histories(alphabet, 1, 2)
    assert result.report["obligations"][0]["cases_total"] == 12


def test_integer_elements_include_both_endpoints_and_combine_with_other_parameters(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.integer(-1, 1), max_size=2)",
        "@tlaw.law\ndef histories(moves: Moves, flag: bool):\n"
        "    record([moves, flag])\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=26)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [
        [history, flag]
        for history in _histories([-1, 0, 1], 0, 2)
        for flag in [False, True]
    ]
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 26
    assert obligation["list_bounds"] == {"moves": {"min_size": 0, "max_size": 2}}


def test_nested_lists_have_13_cases_and_report_each_bound(tmp_path: Path) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.list_of(bool, max_size=1), max_size=2)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=13)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == _histories([[], [False], [True]], 0, 2)
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 13
    assert obligation["list_bounds"] == {
        "moves": {"min_size": 0, "max_size": 2},
        "moves[]": {"min_size": 0, "max_size": 1},
    }


@pytest.mark.parametrize("form", ["explicit", "annotated"])
def test_list_domains_keep_existing_explicit_and_annotated_authoring(
    tmp_path: Path, form: str
) -> None:
    if form == "explicit":
        law = "@tlaw.law('bounded history', moves=Moves)\ndef histories(moves):\n"
    else:
        law = "from typing import Annotated\n\n@tlaw.law\ndef histories(moves: Annotated[list[bool], Moves]):\n"
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(bool, max_size=1)",
        law + "    record(moves)\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[], [False], [True]]
    assert result.report["obligations"][0]["cases_total"] == 3


@pytest.mark.parametrize(
    "declaration, cap",
    [
        ("tlaw.list_of(bool, max_size=4)", 30),
        ("tlaw.list_of(bool, min_size=10**100, max_size=10**100)", 1),
        (f"tlaw.list_of(bool, max_size={sys.maxsize + 1})", 1),
        (f"tlaw.list_of(tlaw.one_of(['only']), max_size={sys.maxsize})", 1),
    ],
)
def test_oversized_list_spaces_are_refused_before_any_law_body(
    tmp_path: Path, declaration: str, cap: int
) -> None:
    law_file = _write_laws(
        tmp_path,
        f"Moves = {declaration}",
        "@tlaw.law\ndef earlier(absent: None):\n    record('called')\n    return True\n\n"
        + _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=cap)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "declaration",
    [
        "tlaw.list_of(bool)",
        "tlaw.list_of(int, max_size=0)",
        "tlaw.list_of(tlaw.number(0, 1), max_size=0)",
        "tlaw.list_of(tlaw.list_of(bool), max_size=0)",
    ],
)
def test_unbounded_or_unsupported_elements_do_not_fall_back_to_sampling(
    tmp_path: Path, declaration: str
) -> None:
    law_file = _write_laws(tmp_path, f"Moves = {declaration}", _passing_law())

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_generated_nested_lists_are_fresh_and_independent_inside_each_case(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.list_of(bool, max_size=1), max_size=2)",
        "@tlaw.law('explicit laws may mutate their generated values', moves=Moves)\n"
        "def histories(moves):\n"
        "    record(moves)\n"
        "    if len(moves) == 2:\n        assert moves[0] is not moves[1]\n"
        "    for move in moves:\n        move.append(9)\n"
        "    moves.clear()\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == _histories([[], [False], [True]], 0, 2)


@pytest.mark.parametrize("element", ["flag", "cycle"])
def test_empty_lists_still_refuse_unsupported_declared_choices(
    tmp_path: Path, element: str
) -> None:
    law_file = _write_laws(
        tmp_path,
        "from enum import IntEnum\n"
        "class Flag(IntEnum):\n    YES = 1\n"
        "flag = Flag.YES\ncycle = []\ncycle.append(cycle)\n"
        f"Moves = tlaw.list_of(tlaw.one_of([{element}]), max_size=0)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert not (tmp_path / "calls.jsonl").exists()


def test_empty_lists_do_not_materialize_an_unused_finite_integer_range(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.integer(0, 10**100), max_size=0)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=1)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[]]
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 1
    assert obligation["exhausted"] is True


def test_repeating_a_declared_mutable_leaf_is_refused_before_execution(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "shared = []\nMoves = tlaw.list_of(tlaw.one_of([shared]), max_size=2)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2, result.output
    assert "shared or cyclic container references" in result.output
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_exclusive_independent_equal_leaf_choices_remain_supported(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.one_of([[], []]), max_size=1)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[], [[]], [[]]]
    assert result.report["obligations"][0]["cases_total"] == 3


def test_list_failure_retains_the_exact_history_and_replays_after_domain_changes(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(tlaw.one_of([False, 0]), max_size=2)",
        "@tlaw.law\ndef histories(moves: Moves):\n"
        "    record([[type(move).__name__, move] for move in moves])\n"
        "    return tlaw.same(all(type(move) is bool for move in moves), True)\n",
    )
    original = law_file.read_text()

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    assert _calls(tmp_path) == [[], [["bool", False]], [["int", 0]]]
    [obligation] = checked.report["obligations"]
    assert obligation["cases_checked"] == 3
    assert obligation["cases_total"] == 7
    assert obligation["exhausted"] is False
    assert obligation["list_bounds"] == {"moves": {"min_size": 0, "max_size": 2}}
    [failure] = checked.report["failures"]
    assert failure["generation"] == "exhaustive"
    assert failure["inputs"] == {
        "moves": {"type": "list", "items": [{"type": "int", "value": "0"}]}
    }
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json())
    law_file.write_text(original.replace("max_size=2", "max_size=0"))
    (tmp_path / "calls.jsonl").unlink()

    replayed = tlaw.replay(report_path)

    assert replayed.exit_code == 0, replayed.output
    assert _calls(tmp_path) == [[["int", 0]]]
    [replay_obligation] = replayed.report["obligations"]
    assert replay_obligation["method"] == "replay"
    assert replay_obligation["cases_checked"] == replay_obligation["cases_total"] == 1
    assert replay_obligation["exhausted"] is False
    assert replay_obligation["list_bounds"] == {}

    law_file.write_text(
        original.replace(
            "return tlaw.same(all(type(move) is bool for move in moves), True)",
            "return True",
        )
    )
    repaired = tlaw.replay(report_path)
    assert repaired.exit_code == 1
    assert "no longer fails" in repaired.output


def test_inferred_list_mutation_guard_keeps_the_original_history(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Moves = tlaw.list_of(bool, min_size=1, max_size=1)",
        "@tlaw.law\ndef histories(moves: Moves):\n    moves.clear()\n    return True\n",
    )

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    assert "passed after mutating generated input" in checked.output
    assert checked.report["failures"][0]["inputs"] == {
        "moves": {"type": "list", "items": [{"type": "bool", "value": False}]}
    }
    assert checked.report["obligations"][0]["cases_checked"] == 1
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json())
    replayed = tlaw.replay(report_path)
    assert replayed.exit_code == 0, replayed.output


def test_sampled_list_checks_keep_unknown_case_counts_and_no_exhaustive_bound(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path, "Moves = tlaw.list_of(bool, max_size=2)", _passing_law()
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0, result.output
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "sampled"
    assert obligation["cases_checked"] is obligation["cases_total"] is None
    assert obligation["exhausted"] is False
    assert obligation["list_bounds"] == {}


def test_bounded_list_laws_still_challenge_implementation_mutations(
    tmp_path: Path,
) -> None:
    implementation = tmp_path / "implementation.py"
    implementation.write_text("def fold(moves):\n    return sum(moves) + 1\n")
    law_file = _write_laws(
        tmp_path,
        "from implementation import fold\nMoves = tlaw.list_of(tlaw.integer(0, 1), max_size=2)",
        "@tlaw.law\ndef histories(moves: Moves):\n    return fold(moves) == sum(moves) + 1\n",
    )

    exhaustive = tlaw.check(law_file, exhaustive=True)
    challenged = mutate(law_file, implementation)

    assert exhaustive.exit_code == 0, exhaustive.output
    assert exhaustive.report["obligations"][0]["cases_total"] == 7
    assert challenged.baseline_valid
    assert challenged.killed_count >= 1
    assert challenged.survived_count == challenged.error_count == 0
    assert all("replay confirmed" in run.note for run in challenged.runs)
