from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
import tlaw

from tlaw.cli import main


def _law_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "laws.py"
    path.write_text(source, encoding="utf-8")
    return path


def _calls(tmp_path: Path) -> list:
    path = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _recorder() -> str:
    return """\
import json
from pathlib import Path
import tlaw

def record(value):
    with Path('calls.jsonl').open('a') as calls:
        calls.write(json.dumps(value) + '\\n')

"""


def test_exhaustive_visits_the_exact_product_including_integer_endpoints(
    tmp_path: Path,
) -> None:
    source = (
        _recorder()
        + """\
from typing import Annotated, Literal

type Flag = bool
Count = tlaw.integer(-1, 1)

@tlaw.law
def product(flag: Flag, absent: None, label: Literal['a', 'b'], count: Annotated[int, Count]):
    record([flag, absent, label, count])
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True, max_cases=12)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [
        list(values)
        for values in itertools.product([False, True], [None], ["a", "b"], [-1, 0, 1])
    ]
    assert result.report["mode"] == "exhaustive"
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "exhaustive"
    assert obligation["cases_checked"] == obligation["cases_total"] == 12
    assert obligation["exhausted"] is True
    assert "exhaustive" in result.output.lower()


def test_one_of_preserves_duplicate_and_type_distinct_choices(tmp_path: Path) -> None:
    source = (
        _recorder()
        + """\
Choices = tlaw.one_of([False, 0, False])

@tlaw.law
def choices(value: Choices):
    record([type(value).__name__, value])
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [["bool", False], ["int", 0], ["bool", False]]
    assert result.report["obligations"][0]["cases_total"] == 3


def test_argument_free_law_has_one_case(tmp_path: Path) -> None:
    source = (
        _recorder()
        + """\
@tlaw.law
def constant():
    record('called')
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == ["called"]
    assert result.report["obligations"][0]["cases_total"] == 1


@pytest.mark.parametrize(
    "declaration, parameters, expression",
    [
        (
            "Shared = tlaw.one_of([shared])",
            "left: Shared, right: Shared",
            "left is not right",
        ),
        (
            "Shared = tlaw.one_of([[shared, shared]])",
            "value: Shared",
            "value[0] is not value[1]",
        ),
    ],
)
def test_container_aliases_are_refused_before_execution(
    tmp_path: Path, declaration: str, parameters: str, expression: str
) -> None:
    source = (
        _recorder()
        + f"""\
shared = []
{declaration}

@tlaw.law
def reference_sensitive({parameters}):
    record('called')
    return {expression}
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert "shared or cyclic container references" in result.output
    assert not (tmp_path / "calls.jsonl").exists()


def test_equal_independent_containers_and_exclusive_aliases_remain_supported(
    tmp_path: Path,
) -> None:
    source = (
        _recorder()
        + """\
shared = []
Left = tlaw.one_of([shared, shared])
Right = tlaw.one_of([[]])

@tlaw.law
def independent(left: Left, right: Right):
    record([left, right])
    return left is not right and left == right
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[[], []], [[], []]]
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 2
    assert obligation["exhausted"] is True


@pytest.mark.parametrize("bound", [6, 10**100])
def test_cap_refusal_preflights_all_laws_before_any_body_runs(
    tmp_path: Path, bound: int
) -> None:
    source = (
        _recorder()
        + f"""\
Value = tlaw.integer(0, {bound})

@tlaw.law
def first(flag: bool):
    record('first law ran')
    return True

@tlaw.law
def oversized(value: Value):
    record('oversized law ran')
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True, max_cases=6)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()
    assert "6" in result.output


@pytest.mark.parametrize(
    "declaration",
    [
        "Value = int",
        "Value = tlaw.number(0, 1)",
        "Value = tlaw.list_of(bool)",
        "Value = tlaw.record(flag=int)",
        "from enum import Enum\nclass Value(Enum):\n    FIRST = 1",
        "from hypothesis import strategies as st\nValue = tlaw.Domain('custom', st.booleans())",
        "class Number(int):\n    pass\nValue = tlaw.one_of([Number(1)])",
        "from enum import IntEnum\nclass Choice(IntEnum):\n    FIRST = 1\nValue = tlaw.one_of([[Choice.FIRST]])",
        "cycle = []\ncycle.append(cycle)\nValue = tlaw.one_of([cycle])",
    ],
)
def test_unsupported_domains_are_invalid_without_sampling_or_body_calls(
    tmp_path: Path, declaration: str
) -> None:
    source = (
        _recorder()
        + declaration
        + """\

@tlaw.law
def unsupported(value: Value):
    record(value)
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_dependent_domains_and_raw_strategies_do_not_fall_back_to_sampling(
    tmp_path: Path,
) -> None:
    sources = [
        """\
Value = tlaw.depends(lambda flag: tlaw.one_of([flag]))

@tlaw.law
def dependent(flag: bool, value: Value):
    record(value)
    return True
""",
        """\
from hypothesis import strategies as st

@tlaw.law('raw strategy', value=st.booleans())
def raw_strategy(value):
    record(value)
    return True
""",
    ]
    for source in sources:
        result = tlaw.check(_law_file(tmp_path, _recorder() + source), exhaustive=True)
        assert result.exit_code == 2
        assert result.report["summary"]["status"] == "invalid"
        assert result.report["failures"] == []
        assert not (tmp_path / "calls.jsonl").exists()


def test_sampled_checks_remain_distinct_even_for_a_finite_type(tmp_path: Path) -> None:
    law_file = _law_file(
        tmp_path,
        "import tlaw\n\n@tlaw.law\ndef truth(flag: bool):\n    return True\n",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0, result.output
    assert result.report["mode"] == "default"
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "sampled"
    assert obligation["cases_checked"] is None
    assert obligation["cases_total"] is None
    assert obligation["exhausted"] is False


@pytest.mark.parametrize(
    ("mark", "status", "checked"),
    [
        ("pytest.mark.skip", "skipped", 0),
        ("pytest.mark.xfail", "xpassed", 2),
    ],
)
def test_pytest_policy_cannot_claim_exhaustion(
    tmp_path: Path, mark: str, status: str, checked: int
) -> None:
    source = f"""\
import pytest
import tlaw

@{mark}
@tlaw.law
def truth(flag: bool):
    return True
"""

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 2
    [obligation] = result.report["obligations"]
    assert obligation["status"] == status
    assert obligation["cases_checked"] == checked
    assert obligation["cases_total"] == 2
    assert obligation["exhausted"] is False


def test_teardown_error_prevents_exhaustion_after_all_cases_run(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture(autouse=True)\ndef boundary():\n"
        "    yield\n    raise RuntimeError('teardown failed')\n",
        encoding="utf-8",
    )
    law_file = _law_file(
        tmp_path,
        "import tlaw\n\n@tlaw.law\ndef truth(flag: bool):\n    return True\n",
    )

    result = tlaw.check(law_file, exhaustive=True)

    assert result.exit_code == 2
    [obligation] = result.report["obligations"]
    assert obligation["status"] == "error"
    assert obligation["cases_checked"] == obligation["cases_total"] == 2
    assert obligation["exhausted"] is False


@pytest.mark.parametrize("mark_inside", [False, True])
def test_skip_marks_survive_both_decorator_orders_for_check_and_replay(
    tmp_path: Path, mark_inside: bool
) -> None:
    base = "import pytest\nimport tlaw\n\n@tlaw.law\ndef refused(flag: bool):\n    return False\n"
    law_file = _law_file(tmp_path, base)
    failed = tlaw.check(law_file, exhaustive=True)
    assert failed.exit_code == 1
    retained = tmp_path / "failure.json"
    retained.write_text(failed.to_json())
    decorators = (
        "@tlaw.law\n@pytest.mark.skip(reason='not attempted')"
        if mark_inside
        else "@pytest.mark.skip(reason='not attempted')\n@tlaw.law"
    )
    law_file.write_text(base.replace("@tlaw.law", decorators))

    for result in (tlaw.check(law_file, exhaustive=True), tlaw.replay(retained)):
        assert result.exit_code == 2, result.output
        [obligation] = result.report["obligations"]
        assert obligation["status"] == "skipped"
        assert obligation["cases_checked"] == 0
        assert obligation["exhausted"] is False
        assert "counterexample reproduced" not in result.output


@pytest.mark.parametrize("mark_inside", [False, True])
def test_function_parametrization_replay_uses_retained_inputs_once(
    tmp_path: Path, mark_inside: bool
) -> None:
    mark = "@pytest.mark.parametrize('offset', [0, 1])"
    law = "@tlaw.law('no change', value=tlaw.integer(0, 1))"
    decorators = f"{law}\n{mark}" if mark_inside else f"{mark}\n{law}"
    source = (
        _recorder()
        + "import pytest\n\n"
        + decorators
        + "\n"
        + """\
def unchanged(value, offset):
    record([value, offset])
    return value + offset == value
"""
    )
    law_file = _law_file(tmp_path, source)
    failed = tlaw.check(law_file, exhaustive=True)
    assert failed.exit_code == 1, failed.output
    retained = tmp_path / "failure.json"
    retained.write_text(failed.to_json())
    (tmp_path / "calls.jsonl").unlink()

    reproduced = tlaw.replay(retained)

    assert reproduced.exit_code == 0, reproduced.output
    assert _calls(tmp_path) == [[0, 1]]
    assert len(reproduced.report["obligations"]) == 1


def test_assume_is_invalid_evidence_instead_of_a_counterexample(tmp_path: Path) -> None:
    source = """\
from hypothesis import assume
import tlaw

@tlaw.law
def filtering(flag: bool):
    assume(flag)
    return True
"""

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert result.report["obligations"][0]["exhausted"] is False


def test_explicit_domains_preserve_pytest_parameters_and_fresh_per_item_counts(
    tmp_path: Path,
) -> None:
    source = (
        _recorder()
        + """\
import pytest

@pytest.fixture
def scale():
    return 2

@pytest.mark.parametrize('offset', [10, 20])
@tlaw.law('fixtures remain available', value=tlaw.integer(0, 1))
def values(value, offset, scale):
    record([offset, value])
    return scale == 2
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[10, 0], [10, 1], [20, 0], [20, 1]]
    assert len(result.report["obligations"]) == 2
    for obligation in result.report["obligations"]:
        assert obligation["cases_checked"] == obligation["cases_total"] == 2
        assert obligation["exhausted"] is True


def test_mutable_choices_are_fresh_for_each_explicit_case(tmp_path: Path) -> None:
    source = (
        _recorder()
        + """\
@tlaw.law('fresh choices', value=tlaw.one_of([[1], [2]]), flag=tlaw.one_of([False, True]))
def values(value, flag):
    record([value, flag])
    value.append(9)
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[[1], False], [[1], True], [[2], False], [[2], True]]


def test_choices_are_snapshotted_before_a_law_can_change_their_source(
    tmp_path: Path,
) -> None:
    source = (
        _recorder()
        + """\
entries = [[0], [1]]
Choices = tlaw.one_of(entries)

@tlaw.law('snapshot choices', value=Choices)
def values(value):
    record(value)
    entries[1][0] = 99
    return True
"""
    )

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 0, result.output
    assert _calls(tmp_path) == [[0], [1]]


def test_inferred_mutation_guard_retains_the_original_input(tmp_path: Path) -> None:
    source = """\
import tlaw
Value = tlaw.one_of([[1]])

@tlaw.law
def clearing_is_identity(value: Value):
    value.clear()
    return True
"""

    result = tlaw.check(_law_file(tmp_path, source), exhaustive=True)

    assert result.exit_code == 1, result.output
    assert "passed after mutating generated input" in result.output
    assert result.report["failures"][0]["inputs"] == {
        "value": {"type": "list", "items": [{"type": "int", "value": "1"}]}
    }
    assert result.report["obligations"][0]["exhausted"] is False


def test_first_failure_stops_enumeration_and_replays_exact_typed_input(
    tmp_path: Path,
) -> None:
    source = (
        _recorder()
        + """\
Value = tlaw.one_of([False, 0, True])

@tlaw.law
def only_booleans(value: Value):
    record([type(value).__name__, value])
    return tlaw.same(type(value).__name__, 'bool')
"""
    )
    law_file = _law_file(tmp_path, source)

    checked = tlaw.check(law_file, exhaustive=True)

    assert checked.exit_code == 1, checked.output
    assert _calls(tmp_path) == [["bool", False], ["int", 0]]
    [obligation] = checked.report["obligations"]
    assert obligation["cases_checked"] == 2
    assert obligation["cases_total"] == 3
    assert obligation["exhausted"] is False
    [failure] = checked.report["failures"]
    assert failure["generation"] == "exhaustive"
    assert failure["inputs"] == {"value": {"type": "int", "value": "0"}}
    assert failure["comparison"]["left"] == "'int'"
    assert failure["comparison"]["right"] == "'bool'"
    report_path = tmp_path / "failure.json"
    report_path.write_text(checked.to_json(), encoding="utf-8")
    (tmp_path / "calls.jsonl").unlink()

    replayed = tlaw.replay(report_path)

    assert replayed.exit_code == 0, replayed.output
    assert _calls(tmp_path) == [["int", 0]]
    [replay_obligation] = replayed.report["obligations"]
    assert replay_obligation["method"] == "replay"
    assert replay_obligation["cases_checked"] == replay_obligation["cases_total"] == 1
    assert replay_obligation["exhausted"] is False

    law_file.write_text(
        source.replace("return tlaw.same(type(value).__name__, 'bool')", "return True"),
        encoding="utf-8",
    )
    repaired = tlaw.replay(report_path)
    assert repaired.exit_code == 1
    assert "no longer fails" in repaired.output


def test_folder_and_cli_exhaustive_modes_preserve_per_law_evidence(
    tmp_path: Path,
) -> None:
    law_file = _law_file(
        tmp_path,
        "import tlaw\n\n@tlaw.law\ndef truth(flag: bool):\n    return True\n",
    )
    folder = tlaw.check_folder(tmp_path, exhaustive=True, max_cases=2)
    assert folder.exit_code == 0
    assert folder.results[0].report["obligations"][0]["exhausted"] is True
    report_path = tmp_path / "success.json"
    assert (
        main(
            [
                "check",
                str(law_file),
                "--exhaustive",
                "--max-cases",
                "2",
                "--json",
                str(report_path),
            ]
        )
        == 0
    )
    report = json.loads(report_path.read_text())
    assert report["mode"] == "exhaustive"
    assert report["obligations"][0]["cases_total"] == 2
    assert main(["check", str(tmp_path), "--exhaustive", "--max-cases", "2"]) == 0


def test_exhaustive_options_cannot_be_silently_ignored(tmp_path: Path) -> None:
    law_file = _law_file(tmp_path, "import tlaw\n")
    with pytest.raises(ValueError):
        tlaw.check(law_file, max_cases=2)
    with pytest.raises(ValueError):
        tlaw.check(law_file, exhaustive=True, search=True)
    with pytest.raises(ValueError):
        tlaw.check(law_file, exhaustive=True, max_cases=True)
    assert main(["check", str(law_file), "--max-cases", "2"]) == 2
    assert main(["check", str(law_file), "--exhaustive", "--max-cases", "0"]) == 2
    with pytest.raises(SystemExit) as conflict:
        main(["check", str(law_file), "--exhaustive", "--search"])
    assert conflict.value.code == 2
