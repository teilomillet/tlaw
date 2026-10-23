from __future__ import annotations

import json
from pathlib import Path

import pytest
import tlaw

from tlaw.audit import ManifestError
from tlaw.cli import main
from tlaw.obligations import read_obligations


PASSING_LAW = """\
import tlaw


@tlaw.law
def adding_zero_changes_nothing(value: int):
    return value + 0 == value
"""

FAILING_LAW = """\
import tlaw


@tlaw.law
def adding_one_changes_nothing(value: int):
    return value + 1 == value
"""


def _law_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "laws.py"
    path.write_text(source, encoding="utf-8")
    return path


def _statuses(result: tlaw.CheckResult) -> list[str]:
    return [obligation["status"] for obligation in result.report["obligations"]]


def test_passing_law_reports_its_executed_obligation(tmp_path: Path) -> None:
    law_file = _law_file(tmp_path, PASSING_LAW)

    result = tlaw.check(law_file)

    assert result.exit_code == 0
    [obligation] = result.report["obligations"]
    assert obligation["identity"] == f"{law_file}::adding_zero_changes_nothing"
    assert obligation["nodeid"].endswith("laws.py::adding_zero_changes_nothing")
    assert obligation["status"] == "passed"


@pytest.mark.parametrize(
    ("mark", "source", "status", "refuted"),
    [
        ('skip(reason="not exercised")', PASSING_LAW, "skipped", False),
        ('xfail(reason="known defect")', FAILING_LAW, "xfailed", True),
        ('xfail(reason="unexpected pass")', PASSING_LAW, "xpassed", False),
        (
            'xfail(reason="unexpected pass", strict=True)',
            PASSING_LAW,
            "xpassed",
            False,
        ),
        (
            'xfail(reason="never called", run=False)',
            FAILING_LAW,
            "xfailed",
            False,
        ),
    ],
)
def test_pytest_policy_cannot_discharge_a_law_obligation(
    tmp_path: Path,
    mark: str,
    source: str,
    status: str,
    refuted: bool,
) -> None:
    source = source.replace(
        "import tlaw", f"import pytest\nimport tlaw\n\npytestmark = pytest.mark.{mark}"
    )
    result = tlaw.check(_law_file(tmp_path, source))

    assert result.exit_code == 2
    assert _statuses(result) == [status]
    assert result.report["summary"]["status"] == ("failed" if refuted else "invalid")
    assert bool(result.report["failures"]) is refuted


def test_ordinary_tests_do_not_supply_missing_law_obligations(
    tmp_path: Path,
) -> None:
    result = tlaw.check(_law_file(tmp_path, "def test_ordinary():\n    assert True\n"))

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["obligations"] == []


@pytest.mark.parametrize("receipt", [None, "not json", "{}"])
def test_missing_or_malformed_execution_evidence_cannot_pass(
    tmp_path: Path, receipt: str | None
) -> None:
    action = "path.unlink()" if receipt is None else f"path.write_text({receipt!r})"
    (tmp_path / "conftest.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_sessionfinish(session):\n"
        "    path = Path(os.environ['TLAW_OBLIGATIONS'])\n"
        f"    {action}\n",
        encoding="utf-8",
    )

    result = tlaw.check(_law_file(tmp_path, PASSING_LAW))

    assert result.exit_code == 2
    assert result.report["summary"]["status"] == "invalid"
    assert "law execution evidence" in result.output


def test_inactive_skip_and_xfail_conditions_allow_normal_execution(
    tmp_path: Path,
) -> None:
    source = PASSING_LAW.replace(
        "import tlaw",
        "import pytest\nimport tlaw\n\n"
        "pytestmark = [pytest.mark.skipif(False, reason='inactive'), "
        "pytest.mark.xfail(False, reason='inactive')]",
    )

    result = tlaw.check(_law_file(tmp_path, source))

    assert result.exit_code == 0
    assert _statuses(result) == ["passed"]


def test_cli_retains_incomplete_law_accounting_without_changing_the_exit_code(
    tmp_path: Path,
) -> None:
    law_file = _law_file(
        tmp_path,
        PASSING_LAW.replace(
            "import tlaw", "import pytest\nimport tlaw\n\npytestmark = pytest.mark.skip"
        ),
    )
    report_path = tmp_path / "incomplete.json"

    assert main(["check", str(law_file), "--json", str(report_path)]) == 2

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "invalid"
    assert [law["status"] for law in report["obligations"]] == ["skipped"]


def test_cli_does_not_retain_an_invalid_run_without_law_accounting(
    tmp_path: Path,
) -> None:
    law_file = _law_file(tmp_path, "def test_ordinary():\n    assert True\n")
    report_path = tmp_path / "no-laws.json"

    assert main(["check", str(law_file), "--json", str(report_path)]) == 2
    assert not report_path.exists()


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_errors_leave_the_law_obligation_incomplete(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = (
        '    raise RuntimeError("fixture setup failed")\n'
        if phase == "setup"
        else '    yield\n    raise RuntimeError("fixture teardown failed")\n'
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture(autouse=True)\ndef boundary():\n" + fixture,
        encoding="utf-8",
    )

    result = tlaw.check(_law_file(tmp_path, PASSING_LAW))

    assert result.exit_code == 2
    assert _statuses(result) == ["error"]
    assert result.report["summary"]["status"] == "invalid"


def test_failfast_keeps_unrun_laws_visible_and_preserves_the_refutation(
    tmp_path: Path,
) -> None:
    (tmp_path / "conftest.py").write_text(
        "def pytest_configure(config):\n    config.option.maxfail = 1\n",
        encoding="utf-8",
    )
    source = FAILING_LAW + "\n" + PASSING_LAW.replace("import tlaw\n", "")

    result = tlaw.check(_law_file(tmp_path, source))

    assert result.exit_code == 2
    assert sorted(_statuses(result)) == ["failed", "not_run"]
    assert result.report["summary"] == {
        "failures": 1,
        "status": "failed",
        "retained": True,
    }
    assert result.report["failures"][0]["inputs"] is not None


def test_parametrized_law_cases_have_distinct_obligations(tmp_path: Path) -> None:
    law_file = _law_file(
        tmp_path,
        """\
import pytest
import tlaw
from hypothesis import strategies as st


@pytest.mark.parametrize("offset", [0, pytest.param(1, marks=pytest.mark.skip)])
@tlaw.law("adding zero is identity", value=st.integers())
def adding_zero(value, offset):
    return value + offset == value
""",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 2
    obligations = result.report["obligations"]
    assert sorted(_statuses(result)) == ["passed", "skipped"]
    assert len({obligation["nodeid"] for obligation in obligations}) == 2
    assert {obligation["identity"] for obligation in obligations} == {
        f"{law_file}::adding_zero"
    }


def _retained_report(tmp_path: Path) -> tuple[Path, Path, dict]:
    law_file = _law_file(tmp_path, FAILING_LAW)
    checked = tlaw.check(law_file)
    assert checked.exit_code == 1
    report = json.loads(checked.to_json())
    # Retained v1 evidence predates obligation accounting and remains replayable.
    report.pop("obligations", None)
    report_path = tmp_path / "failure.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return law_file, report_path, report


def test_replay_of_an_old_report_returns_fresh_obligations(tmp_path: Path) -> None:
    _, report_path, retained = _retained_report(tmp_path)

    result = tlaw.replay(report_path)

    assert result.exit_code == 0
    assert "1 retained counterexample reproduced" in result.output
    assert _statuses(result) == ["passed"]
    assert result.report["failures"] == retained["failures"]


@pytest.mark.parametrize(
    ("mark", "status"),
    [
        ('skip(reason="not replayed")', "skipped"),
        ('xfail(reason="expected reproduction")', "xpassed"),
        ('xfail(reason="never replayed", run=False)', "xfailed"),
    ],
)
def test_replay_does_not_claim_skipped_or_expected_failure_reproduction(
    tmp_path: Path,
    mark: str,
    status: str,
) -> None:
    law_file, report_path, retained = _retained_report(tmp_path)
    law_file.write_text(
        FAILING_LAW.replace(
            "import tlaw",
            f"import pytest\nimport tlaw\n\npytestmark = pytest.mark.{mark}",
        ),
        encoding="utf-8",
    )

    result = tlaw.replay(report_path)

    assert result.exit_code == 2
    assert "retained counterexample reproduced" not in result.output
    assert _statuses(result) == [status]
    assert result.report["failures"] == retained["failures"]


def test_replay_mismatch_is_a_failed_completed_obligation(tmp_path: Path) -> None:
    law_file, report_path, retained = _retained_report(tmp_path)
    law_file.write_text(
        FAILING_LAW.replace("return value + 1 == value", "return True"),
        encoding="utf-8",
    )

    result = tlaw.replay(report_path)

    assert result.exit_code == 1
    assert "retained counterexample no longer fails" in result.output
    assert _statuses(result) == ["failed"]
    assert result.report["failures"] == retained["failures"]


@pytest.mark.parametrize(
    "bounds",
    [
        None,
        [],
        {"": {"min_size": 0, "max_size": 1}},
        {"moves": {"max_size": 1}},
        {"moves": {"min_size": False, "max_size": 1}},
        {"moves": {"min_size": 0, "max_size": 1.0}},
        {"moves": {"min_size": -1, "max_size": 1}},
        {"moves": {"min_size": 2, "max_size": 1}},
    ],
)
def test_malformed_list_bounds_cannot_justify_execution_evidence(
    tmp_path: Path, bounds: object
) -> None:
    receipt = tmp_path / "obligations.json"
    receipt.write_text(
        json.dumps(
            [
                {
                    "identity": "laws.py::bounded",
                    "nodeid": "laws.py::bounded",
                    "status": "passed",
                    "method": "exhaustive",
                    "cases_checked": 1,
                    "cases_total": 1,
                    "exhausted": True,
                    "list_bounds": bounds,
                }
            ]
        )
    )

    with pytest.raises(ManifestError, match="invalid list bounds"):
        read_obligations(receipt)
