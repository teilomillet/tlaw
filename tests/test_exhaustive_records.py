"""Independent public-path checks for finite records and bounded histories."""

from __future__ import annotations

import itertools
import json
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

def record_call(value):
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


def _passing_law(parameter: str = "request: Request") -> str:
    name = parameter.partition(":")[0]
    return (
        f"@tlaw.law\ndef accepts({parameter}):\n"
        f"    record_call({name})\n    return True\n"
    )


COMMAND = (
    "Command = tlaw.record(kind=tlaw.one_of(['credit', 'debit']), "
    "amount=tlaw.integer(0, 2))"
)
COMMANDS = [
    {"kind": kind, "amount": amount}
    for kind in ["credit", "debit"]
    for amount in [0, 1, 2]
]


def test_record_enumerates_all_six_commands_in_field_declaration_order(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        COMMAND,
        "@tlaw.law\ndef accepts(command: Command):\n"
        "    record_call([list(command), command])\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=6)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[["kind", "amount"], command] for command in COMMANDS]
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "exhaustive"
    assert obligation["cases_checked"] == obligation["cases_total"] == 6
    assert obligation["exhausted"] is True
    assert obligation["list_bounds"] == {}


def test_record_histories_check_all_43_cases_and_report_bounds_in_cli_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    law_file = _write_laws(
        tmp_path,
        COMMAND + "\nHistory = tlaw.list_of(Command, max_size=2)",
        _passing_law("history: History"),
    )
    report_path = tmp_path / "report.json"

    assert (
        main(
            [
                "check",
                str(law_file),
                "--exhaustive",
                "--max-cases",
                "43",
                "--json",
                str(report_path),
            ]
        )
        == 0
    )

    assert _calls(tmp_path) == _histories(COMMANDS, 0, 2)
    [obligation] = json.loads(report_path.read_text())["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 43
    assert obligation["exhausted"] is True
    assert obligation["list_bounds"] == {"history": {"min_size": 0, "max_size": 2}}
    output = capsys.readouterr().out
    assert "43/43 cases checked" in output
    assert "lengths: history=0..2" in output


def test_nested_records_and_lists_report_each_structural_bound(tmp_path: Path) -> None:
    law_file = _write_laws(
        tmp_path,
        "Options = tlaw.record(flags=tlaw.list_of(bool, max_size=1))\n"
        "Request = tlaw.record(options=Options, active=bool)\n"
        "History = tlaw.list_of(Request, max_size=1)",
        "@tlaw.law\ndef accepts(request: Request, history: History):\n"
        "    record_call([request, history])\n    return True\n",
    )
    requests = [
        {"options": {"flags": flags}, "active": active}
        for flags in [[], [False], [True]]
        for active in [False, True]
    ]

    result = tlaw.check(law_file, exhaustive=True, max_cases=42)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [
        [request, history]
        for request in requests
        for history in _histories(requests, 0, 1)
    ]
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 42
    assert obligation["list_bounds"] == {
        "request.options.flags": {"min_size": 0, "max_size": 1},
        "history": {"min_size": 0, "max_size": 1},
        "history[].options.flags": {"min_size": 0, "max_size": 1},
    }


def test_special_field_names_have_unambiguous_bound_paths(tmp_path: Path) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.record(**{\n"
        "    'a.b': tlaw.list_of(bool, max_size=0),\n"
        "    'a': tlaw.record(b=tlaw.list_of(bool, max_size=1)),\n"
        "    'quote\"key': tlaw.list_of(bool, max_size=0),\n"
        "    'items[]': tlaw.list_of(bool, max_size=0),\n"
        "    '': tlaw.list_of(bool, max_size=0),\n"
        "})",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert result.report["obligations"][0]["cases_total"] == 3
    assert result.report["obligations"][0]["list_bounds"] == {
        'request["a.b"]': {"min_size": 0, "max_size": 0},
        "request.a.b": {"min_size": 0, "max_size": 1},
        'request["quote\\"key"]': {"min_size": 0, "max_size": 0},
        'request["items[]"]': {"min_size": 0, "max_size": 0},
        'request[""]': {"min_size": 0, "max_size": 0},
    }


def test_record_fields_keep_duplicate_and_type_distinct_leaf_entries(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.record(value=tlaw.one_of([False, 0, False]), active=bool)",
        "@tlaw.law\ndef accepts(request: Request):\n"
        "    record_call([type(request['value']).__name__, request])\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [
        [kind, {"value": value, "active": active}]
        for kind, value in [("bool", False), ("int", 0), ("bool", False)]
        for active in [False, True]
    ]
    assert result.report["obligations"][0]["cases_total"] == 6


@pytest.mark.parametrize("cap", [5, 42])
def test_record_product_and_history_budgets_are_checked_before_any_law_body(
    tmp_path: Path, cap: int
) -> None:
    annotation = "Command" if cap == 5 else "History"
    law_file = _write_laws(
        tmp_path,
        COMMAND + "\nHistory = tlaw.list_of(Command, max_size=2)",
        "@tlaw.law\ndef earlier(value: None):\n    record_call('called')\n    return True\n\n"
        + _passing_law(f"request: {annotation}"),
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=cap)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "declaration",
    [
        "tlaw.record(value=int)",
        "tlaw.record(value=tlaw.number(0, 1))",
        "tlaw.record(value=tlaw.list_of(bool))",
        "tlaw.record(dict, value=bool)",
        "tlaw.record(value=bool, other=tlaw.depends(lambda value: tlaw.one_of([value])))",
    ],
)
@pytest.mark.parametrize("empty_outer", [False, True])
def test_unsupported_record_fields_and_constructors_never_fall_back_to_sampling(
    tmp_path: Path, declaration: str, empty_outer: bool
) -> None:
    if empty_outer:
        declaration = f"tlaw.list_of({declaration}, max_size=0)"
    law_file = _write_laws(
        tmp_path,
        f"Request = {declaration}",
        "@tlaw.law\ndef earlier(value: None):\n    record_call('called')\n    return True\n\n"
        + _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_empty_history_does_not_materialize_unused_huge_record_fields(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.list_of(tlaw.record(value=tlaw.integer(0, 10**100)), max_size=0)",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True, max_cases=1)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[]]
    assert result.report["obligations"][0]["cases_total"] == 1


def test_derived_record_containers_are_fresh_inside_and_between_cases(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Cell = tlaw.record(flags=tlaw.list_of(bool, max_size=1))\n"
        "Request = tlaw.record(left=Cell, right=Cell)",
        "@tlaw.law('explicit laws may mutate generated values', request=Request)\n"
        "def accepts(request):\n"
        "    record_call(request)\n"
        "    assert request['left'] is not request['right']\n"
        "    assert request['left']['flags'] is not request['right']['flags']\n"
        "    request['left']['flags'].append(9)\n"
        "    request['right'].clear()\n    request.clear()\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [
        {"left": {"flags": left}, "right": {"flags": right}}
        for left in [[], [False], [True]]
        for right in [[], [False], [True]]
    ]
    assert result.report["obligations"][0]["cases_total"] == 9


@pytest.mark.parametrize("shape", ["fields", "history"])
def test_shared_declared_mutable_leaves_are_refused_before_execution(
    tmp_path: Path, shape: str
) -> None:
    declaration = (
        "tlaw.record(left=Leaf, right=Leaf)"
        if shape == "fields"
        else "tlaw.list_of(tlaw.record(value=Leaf), min_size=2, max_size=2)"
    )
    law_file = _write_laws(
        tmp_path,
        f"shared = []\nLeaf = tlaw.one_of([shared])\nRequest = {declaration}",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2, result.output
    assert "shared or cyclic container references" in result.output
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_exclusive_equal_mutable_record_choices_remain_supported(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.record(value=tlaw.one_of([[], []]))",
        _passing_law(),
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [{"value": []}, {"value": []}]
    assert result.report["obligations"][0]["cases_total"] == 2


@pytest.mark.parametrize("history", [False, True])
def test_exact_typed_record_failures_replay_without_regeneration_and_notice_repairs(
    tmp_path: Path, history: bool
) -> None:
    shape = "tlaw.list_of(Item, max_size=1)" if history else "Item"
    actual = (
        "all(type(item['value']) is bool for item in request)"
        if history
        else "type(request['value']) is bool"
    )
    law_file = _write_laws(
        tmp_path,
        f"Item = tlaw.record(value=tlaw.one_of([False, 0]))\nRequest = {shape}",
        "@tlaw.law\ndef accepts(request: Request):\n"
        "    record_call(request)\n"
        f"    return tlaw.same({actual}, True)\n",
    )
    original = law_file.read_text()

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    [obligation] = checked.report["obligations"]
    assert (
        obligation["cases_checked"]
        == obligation["cases_total"]
        == (3 if history else 2)
    )
    assert obligation["exhausted"] is False
    [failure] = checked.report["failures"]
    encoded_record = {
        "type": "dict",
        "entries": [
            {
                "key": {"type": "str", "value": "value"},
                "value": {"type": "int", "value": "0"},
            }
        ],
    }
    assert failure["generation"] == "exhaustive"
    assert failure["inputs"] == {
        "request": {"type": "list", "items": [encoded_record]}
        if history
        else encoded_record
    }
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json())
    law_file.write_text(original.replace("[False, 0]", "[False]"))
    (tmp_path / "calls.jsonl").unlink()

    replayed = tlaw.replay(report_path)

    assert replayed.exit_code == 0, replayed.output
    assert _calls(tmp_path) == ([[{"value": 0}]] if history else [{"value": 0}])
    [replayed_obligation] = replayed.report["obligations"]
    assert replayed_obligation["method"] == "replay"
    assert (
        replayed_obligation["cases_checked"] == replayed_obligation["cases_total"] == 1
    )
    assert replayed_obligation["exhausted"] is False
    assert replayed_obligation["list_bounds"] == {}
    law_file.write_text(
        original.replace(f"return tlaw.same({actual}, True)", "return True")
    )

    repaired = tlaw.replay(report_path)

    assert repaired.exit_code == 1
    assert "no longer fails" in repaired.output


def test_inferred_record_mutation_guard_retains_original_nested_input(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.record(options=tlaw.record(flags=tlaw.list_of(bool, max_size=0)))",
        "@tlaw.law\ndef accepts(request: Request):\n"
        "    request['options']['flags'].append(True)\n    return True\n",
    )

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    assert "passed after mutating generated input" in checked.output
    assert checked.report["obligations"][0]["cases_checked"] == 1
    assert checked.report["failures"][0]["inputs"] == {
        "request": {
            "type": "dict",
            "entries": [
                {
                    "key": {"type": "str", "value": "options"},
                    "value": {
                        "type": "dict",
                        "entries": [
                            {
                                "key": {"type": "str", "value": "flags"},
                                "value": {"type": "list", "items": []},
                            }
                        ],
                    },
                }
            ],
        }
    }
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json())
    replayed = tlaw.replay(report_path)
    assert replayed.exit_code == 0, replayed.output


def test_sampled_records_do_not_claim_counts_or_exhaustive_bounds(
    tmp_path: Path,
) -> None:
    law_file = _write_laws(
        tmp_path,
        "Request = tlaw.record(flags=tlaw.list_of(bool, max_size=1))",
        _passing_law(),
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0, result.output
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "sampled"
    assert obligation["cases_checked"] is obligation["cases_total"] is None
    assert obligation["exhausted"] is False
    assert obligation["list_bounds"] == {}


def test_folder_cli_reports_nested_bounds_and_partial_case_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_laws(
        tmp_path,
        "Request = tlaw.record(flags=tlaw.list_of(bool, max_size=1))",
        "@tlaw.law\ndef passes(request: Request):\n    return True\n\n"
        "@tlaw.law\ndef fails(request: Request):\n    return not request['flags']\n",
    )

    assert main(["check", str(tmp_path), "--exhaustive"]) == 1

    output = capsys.readouterr().out
    assert (
        "3/3 cases checked; lengths: request.flags=0..1; all declared cases passed"
        in output
    )
    assert "2/3 cases checked; lengths: request.flags=0..1; not exhausted" in output


def test_43_histories_reject_a_known_wrong_debit_transition_and_replay_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "implementation.py").write_text(
        "def balance(history):\n"
        "    balance = 0\n"
        "    for command in history:\n"
        "        balance += command['amount']\n"
        "    return balance\n"
    )
    law_file = _write_laws(
        tmp_path,
        "from implementation import balance\n"
        + COMMAND
        + "\nHistory = tlaw.list_of(Command, max_size=2)",
        "@tlaw.law\ndef account(history: History):\n"
        "    expected = sum(\n"
        "        command['amount'] if command['kind'] == 'credit' else -command['amount']\n"
        "        for command in history\n"
        "    )\n"
        "    return tlaw.same(balance(history), expected)\n",
    )

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    [obligation] = checked.report["obligations"]
    assert obligation["cases_checked"] == 6
    assert obligation["cases_total"] == 43
    assert obligation["exhausted"] is False
    assert obligation["list_bounds"] == {"history": {"min_size": 0, "max_size": 2}}
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json())

    replayed = tlaw.replay(report_path)

    assert replayed.exit_code == 0, replayed.output
    assert replayed.report["obligations"][0]["cases_checked"] == 1


def test_record_history_laws_keep_implementation_mutation_and_replay_challenges(
    tmp_path: Path,
) -> None:
    implementation = tmp_path / "implementation.py"
    implementation.write_text(
        "def total(history):\n    return sum(item['amount'] for item in history) + 1\n"
    )
    law_file = _write_laws(
        tmp_path,
        "from implementation import total\n"
        "Item = tlaw.record(amount=tlaw.integer(0, 1))\n"
        "History = tlaw.list_of(Item, max_size=2)",
        "@tlaw.law\ndef accepts(history: History):\n"
        "    return total(history) == sum(item['amount'] for item in history) + 1\n",
    )

    checked = tlaw.check(law_file, exhaustive=True)
    challenged = mutate(law_file, implementation)

    assert checked.exit_code == 0, checked.output
    assert checked.report["obligations"][0]["cases_total"] == 7
    assert challenged.baseline_valid
    assert challenged.killed_count >= 1
    assert challenged.survived_count == challenged.error_count == 0
    assert all("replay confirmed" in run.note for run in challenged.runs)
