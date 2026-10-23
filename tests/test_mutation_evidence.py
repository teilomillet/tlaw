"""Challenge mutation evidence with real, independently written law files.

The one-site implementation has one mutation: return 1 becomes return 2.
These tests assert the evidence contract through the public mutation result;
they do not reuse its outcome classifier to calculate expected answers.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

import tlaw.mutation as mutation
from tlaw.mutation import MutationOutcome, mutate
from tlaw.obligations import OBLIGATIONS_ENV
from tlaw.replay import CAPTURE_ENV


IMPLEMENTATION = "def value():\n    return 1\n"
IMPORTS = "import tlaw\nfrom implementation import value\n"
PASSING_LAW = """
@tlaw.law
def value_is_one(flag: bool):
    return value() == 1
"""


def _boundary(tmp_path: Path, laws: str) -> tuple[Path, Path]:
    implementation = tmp_path / "implementation.py"
    implementation.write_text(IMPLEMENTATION, encoding="utf-8")
    law_file = tmp_path / "laws.py"
    law_file.write_text(textwrap.dedent(laws), encoding="utf-8")
    return law_file, implementation


@pytest.mark.parametrize("marker", ["skip", "xfail"])
def test_partial_baseline_cannot_support_mutation(tmp_path: Path, marker: str) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "import pytest\n"
        + PASSING_LAW
        + f"""
@pytest.mark.{marker}(reason="intentionally incomplete")
@tlaw.law
def another_obligation(flag: bool):
    return False
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline.exit_code == 0
    assert not result.baseline_valid
    assert len(result.baseline.obligations) == 2
    assert result.canary is None
    assert result.killed_count == result.survived_count == 0
    assert all(run.outcome is MutationOutcome.NOT_RUN for run in result.runs)


def test_ordinary_pytest_success_is_not_a_law_baseline(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        "def test_ordinary_success():\n    assert True\n",
    )

    result = mutate(laws, implementation)

    assert result.baseline.exit_code == 0
    assert not result.baseline_valid
    assert result.baseline.obligations == ()
    assert result.canary is None
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("receipt", [None, "not valid JSON", "{}"])
def test_missing_or_malformed_baseline_receipt_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt: str | None
) -> None:
    laws, implementation = _boundary(tmp_path, IMPORTS + PASSING_LAW)

    def child(command, **kwargs):
        destination = Path(kwargs["env"][OBLIGATIONS_ENV])
        if receipt is not None:
            destination.write_text(receipt, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation)

    assert not result.baseline_valid
    assert result.baseline.evidence_error
    assert result.canary is None
    assert result.killed_count == result.survived_count == 0


def test_missing_canary_receipt_does_not_establish_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(tmp_path, IMPORTS + PASSING_LAW)
    run_child = mutation.subprocess.run

    def child(command, **kwargs):
        completed = run_child(command, **kwargs)
        source = (Path(kwargs["cwd"]) / "implementation.py").read_text(encoding="utf-8")
        if "tlaw canary" in source:
            Path(kwargs["env"][OBLIGATIONS_ENV]).unlink()
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None
    assert result.canary.wired is None
    assert result.canary.evidence_error
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize(
    "capture", [None, '{"schema": "tlaw.capture/v1", "failures": [7]}']
)
def test_missing_or_malformed_mutant_counterexample_cannot_establish_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capture: str | None
) -> None:
    laws, implementation = _boundary(tmp_path, IMPORTS + PASSING_LAW)
    run_child = mutation.subprocess.run

    def child(command, **kwargs):
        completed = run_child(command, **kwargs)
        source = (Path(kwargs["cwd"]) / "implementation.py").read_text(encoding="utf-8")
        if "return 2" in source:
            destination = Path(kwargs["env"][CAPTURE_ENV])
            if capture is None:
                destination.unlink()
            else:
                destination.write_text(capture, encoding="utf-8")
        return completed

    monkeypatch.setattr(mutation.subprocess, "run", child)

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert result.runs[0].outcome is MutationOutcome.ERROR
    assert result.runs[0].replay_method is None
    assert result.killed_count == 0


def test_opaque_input_cannot_support_exact_replay(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + """
from hypothesis import strategies as st

@tlaw.law("opaque input", token=st.just(object()))
def opaque_input(token):
    return value() == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    run = result.runs[0]
    assert run.outcome is MutationOutcome.ERROR
    assert run.evidence["failures"][0]["inputs"] is None
    assert "safe replay encoding" in run.evidence["failures"][0]["capture_error"]
    assert run.replay_method is None
    assert result.killed_count == 0


@pytest.mark.parametrize("failure", ["ordinary_test", "fixture_setup"])
def test_canary_requires_a_law_refutation(tmp_path: Path, failure: str) -> None:
    neighboring_failure = """
def test_neighbor():
    assert value() == 1
"""
    fixture_failure = """
import pytest

@pytest.fixture(autouse=True)
def setup_implementation():
    value()
"""
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + """
@tlaw.law
def independent_obligation(flag: bool):
    return True
"""
        + (neighboring_failure if failure == "ordinary_test" else fixture_failure),
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None
    assert result.canary.exit_code == 1
    assert result.canary.wired is None
    assert result.canary.evidence_error
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("action", ["skip", "xfail"])
def test_mutant_cannot_survive_by_avoiding_a_law(tmp_path: Path, action: str) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "import pytest\n"
        + f"""
@tlaw.law
def must_execute(flag: bool):
    observed = value()
    if observed == 2:
        pytest.{action}("mutant avoids the obligation")
    return observed == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert len(result.runs) == 1
    assert result.runs[0].outcome is MutationOutcome.ERROR
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_error_is_not_a_law_refutation(tmp_path: Path, phase: str) -> None:
    before = 'raise RuntimeError("broken setup")' if phase == "setup" else "pass"
    after = 'raise RuntimeError("broken teardown")' if phase == "teardown" else "pass"
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "import inspect\nimport pytest\n"
        + f"""
@pytest.fixture(autouse=True)
def lifecycle():
    changed = "return 2" in inspect.getsource(value)
    if changed:
        {before}
    yield
    if changed:
        {after}

@tlaw.law
def value_is_observed(flag: bool):
    value()
    return True
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert result.runs[0].outcome is MutationOutcome.ERROR
    assert result.killed_count == 0


def test_ordinary_pytest_failure_cannot_supply_a_law_kill(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + """
@tlaw.law
def implementation_is_observed(flag: bool):
    value()
    return True

def test_ordinary_failure():
    assert value() == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert result.runs[0].outcome is MutationOutcome.ERROR
    assert result.killed_count == 0


def test_mutant_cannot_change_the_collected_law_inventory(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "import inspect\n"
        + """
@tlaw.law
def always_collected(flag: bool):
    value()
    return True

if "return 2" not in inspect.getsource(value):
    @tlaw.law
    def sometimes_collected(flag: bool):
        return value() == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert len(result.baseline.obligations) == 2
    assert result.canary is not None and result.canary.wired is True
    assert result.runs[0].outcome is MutationOutcome.ERROR
    assert result.killed_count == result.survived_count == 0


@pytest.mark.parametrize("packaged", [False, True])
def test_exact_replay_uses_retained_typed_input_in_a_fresh_copy(
    tmp_path: Path, packaged: bool
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + """
import os
from hypothesis import strategies as st
from pathlib import Path

# Reusing any previous execution directory would fail at collection.
assert not Path("law-run-marker").exists()
Path("law-run-marker").write_text("isolated process", encoding="utf-8")

PAYLOAD = {"pair": (b"saved", False), "values": [0, True]}
# Exact replay succeeds even when no fresh example can be generated.
inputs = st.nothing() if os.environ.get("TLAW_REPLAY") else st.just(PAYLOAD)

@tlaw.law("nested input is retained", payload=inputs)
def nested_input_is_retained(payload):
    assert payload == PAYLOAD
    return value() == 1
""",
    )
    if packaged:
        package = tmp_path / "specimen"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        target = package / "implementation.py"
        implementation.rename(target)
        implementation = target
        laws.write_text(
            laws.read_text(encoding="utf-8").replace(
                "from implementation import value",
                "from specimen.implementation import value",
            ),
            encoding="utf-8",
        )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert len(result.runs) == 1
    run = result.runs[0]
    assert run.outcome is MutationOutcome.KILLED
    assert run.replay_method == "exact"
    assert run.replay_exit_code == 0
    assert run.replay_timed_out is False
    assert run.replay_duration_seconds > 0
    assert run.evidence["schema"] == "tlaw.check/v1"
    failure = run.evidence["failures"][0]
    assert failure["identity"] == "laws.py::nested_input_is_retained"
    assert failure["source"]["path"] == "laws.py"
    assert failure["inputs"] == {
        "payload": {
            "type": "dict",
            "entries": [
                {
                    "key": {"type": "str", "value": "pair"},
                    "value": {
                        "type": "tuple",
                        "items": [
                            {"type": "bytes", "base64": "c2F2ZWQ="},
                            {"type": "bool", "value": False},
                        ],
                    },
                },
                {
                    "key": {"type": "str", "value": "values"},
                    "value": {
                        "type": "list",
                        "items": [
                            {"type": "int", "value": "0"},
                            {"type": "bool", "value": True},
                        ],
                    },
                },
            ],
        }
    }
    replayed = run.replay_evidence["obligations"]
    assert len(replayed) == 1
    assert replayed[0]["method"] == "replay"
    assert replayed[0]["status"] == "passed"
    assert replayed[0]["cases_checked"] == replayed[0]["cases_total"] == 1
    encoded = result.to_dict()
    assert encoded["mutations"][0]["evidence"] == run.evidence
    assert encoded["mutations"][0]["replay_evidence"] == run.replay_evidence
    assert encoded["mutations"][0]["replay_exit_code"] == 0
    assert encoded["mutations"][0]["replay_timed_out"] is False
    assert encoded["mutations"][0]["replay_duration_seconds"] > 0
    assert implementation.read_text(encoding="utf-8") == IMPLEMENTATION
    assert not (tmp_path / "law-run-marker").exists()


def test_a_different_replay_exception_cannot_confirm_the_kill(tmp_path: Path) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "import os\n"
        + """
@tlaw.law
def fingerprint_must_match(flag: bool):
    observed = value()
    if observed == 2:
        if os.environ.get("TLAW_REPLAY"):
            raise ValueError("different failure")
        raise AssertionError("original failure")
    return observed == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    run = result.runs[0]
    assert run.outcome is MutationOutcome.ERROR
    assert run.replay_method == "exact"
    assert "replay confirmed" not in run.note
    assert result.killed_count == 0
    assert run.replay_exit_code == 1
    assert run.replay_timed_out is False
    assert run.replay_duration_seconds > 0


def test_ordinary_failure_cannot_confirm_a_timeout(tmp_path: Path) -> None:
    # This deliberate external observation distinguishes two fresh processes.
    # A different failure in the second process must not confirm the timeout.
    marker = tmp_path / "timeout-observed"
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + "from pathlib import Path\n"
        + f"""
@tlaw.law
def timeout_must_repeat(flag: bool):
    observed = value()
    if observed == 2:
        marker = Path({str(marker)!r})
        if marker.exists():
            raise AssertionError("ordinary failure instead of timeout")
        marker.write_text("first process timed out", encoding="utf-8")
        while True:
            pass
    return observed == 1
""",
    )

    result = mutate(
        laws,
        implementation,
        timeout_floor_seconds=1.0,
        timeout_multiplier=2.0,
    )

    assert result.baseline_valid
    run = result.runs[0]
    assert marker.exists()
    assert run.timed_out
    assert run.outcome is MutationOutcome.ERROR
    assert run.replay_method == "timeout"
    assert "replay confirmed" not in run.note
    assert result.killed_count == 0
    assert run.replay_exit_code == 1
    assert run.replay_timed_out is False
    assert run.replay_duration_seconds > 0


@pytest.mark.parametrize("remove_case", [False, True])
def test_parametrized_laws_retain_each_item_for_exact_replay(
    tmp_path: Path, remove_case: bool
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + f"""
import inspect
import pytest

cases = ["first", "second"]
if {remove_case!r} and "return 2" in inspect.getsource(value):
    cases = ["first"]

@pytest.mark.parametrize("case", cases)
@tlaw.law("parametrized obligation", flag=tlaw.one_of([False]))
def parametrized_obligation(case, flag):
    assert case in {{"first", "second"}}
    return value() == 1
""",
    )

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert len(result.baseline.obligations) == 2
    assert result.canary is not None and result.canary.wired is True
    run = result.runs[0]
    if remove_case:
        assert run.outcome is MutationOutcome.ERROR
        assert run.replay_method is None
        assert "inventory" in run.note
        return
    assert run.outcome is MutationOutcome.KILLED
    assert run.replay_method == "exact"
    assert len(run.evidence["failures"]) == 2
    assert {
        failure["inputs"]["case"]["value"] for failure in run.evidence["failures"]
    } == {"first", "second"}
    assert len(run.replay_evidence["obligations"]) == 1
    replayed = run.replay_evidence["obligations"][0]
    assert replayed["method"] == "replay"
    assert replayed["status"] == "passed"
    assert replayed["cases_checked"] == replayed["cases_total"] == 2


def test_inherited_check_environment_cannot_change_mutation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laws, implementation = _boundary(
        tmp_path,
        IMPORTS
        + """
import os

assert os.environ.get("TLAW_MODE") in {None, "default"}
assert os.environ.get("TLAW_SEED") is None
assert os.environ.get("TLAW_MAX_CASES") is None
assert os.environ.get("TLAW_EXPLAIN") is None
if not os.environ.get("TLAW_REPLAY"):
    assert os.environ.get("TLAW_REPLAY_ROOT") is None
"""
        + PASSING_LAW,
    )
    foreign_receipt = tmp_path / "unrelated-evidence.json"
    foreign_receipt.write_text("unrelated evidence", encoding="utf-8")
    for name, value in {
        "TLAW_MODE": "exhaustive",
        "TLAW_SEED": "not a seed",
        "TLAW_MAX_CASES": "not a case limit",
        "TLAW_EXPLAIN": "1",
        "TLAW_REPLAY": str(foreign_receipt),
        "TLAW_REPLAY_ROOT": "/wrong-replay-root",
        "TLAW_CAPTURE": str(foreign_receipt),
        "TLAW_OBLIGATIONS": str(foreign_receipt),
    }.items():
        monkeypatch.setenv(name, value)

    result = mutate(laws, implementation)

    assert result.baseline_valid
    assert result.baseline.obligations[0].method == "sampled"
    assert result.canary is not None and result.canary.wired is True
    assert result.runs[0].outcome is MutationOutcome.KILLED
    assert result.runs[0].replay_method == "exact"
    assert foreign_receipt.read_text(encoding="utf-8") == "unrelated evidence"
