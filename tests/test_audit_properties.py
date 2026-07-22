from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tlaw.audit import AuditResult, EnforcementError, KnownWrongOutcome, RunResult


def run(name: str, exit_code: int) -> RunResult:
    return RunResult(
        name=name,
        target=f"wallets:{name}",
        exit_code=exit_code,
        failed_tests=("test_wallet_law",) if exit_code == 1 else (),
        output="fixed evidence",
    )


@given(exit_code=st.integers(min_value=0, max_value=5))
def test_baseline_is_valid_only_after_a_successful_test_run(exit_code: int) -> None:
    result = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", exit_code),
        known_wrong=(run("wrong", 1),),
    )

    assert result.baseline_valid is (exit_code == 0)


@given(exit_code=st.integers(min_value=0, max_value=5))
def test_only_a_test_failure_rejects_a_known_wrong_implementation(
    exit_code: int,
) -> None:
    result = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", 0),
        known_wrong=(run("wrong", exit_code),),
    )

    expected = {
        0: KnownWrongOutcome.SURVIVED,
        1: KnownWrongOutcome.REJECTED,
    }.get(exit_code, KnownWrongOutcome.ERROR)
    assert result.outcome_for("wrong") is expected


@given(
    outcomes=st.lists(st.booleans(), min_size=1, max_size=20),
    survivor_index=st.integers(min_value=0, max_value=19),
)
def test_rejecting_one_survivor_increases_the_rejected_count_by_one(
    outcomes: list[bool], survivor_index: int
) -> None:
    survivor_index %= len(outcomes)
    outcomes[survivor_index] = False
    before = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", 0),
        known_wrong=tuple(
            run(f"wrong-{index}", 1 if rejected else 0)
            for index, rejected in enumerate(outcomes)
        ),
    )

    outcomes[survivor_index] = True
    after = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", 0),
        known_wrong=tuple(
            run(f"wrong-{index}", 1 if rejected else 0)
            for index, rejected in enumerate(outcomes)
        ),
    )

    assert after.rejected_count == before.rejected_count + 1
    assert after.score > before.score


@given(
    exit_codes=st.lists(st.integers(min_value=0, max_value=5), min_size=0, max_size=20)
)
def test_report_is_deterministic_for_the_same_evidence(
    exit_codes: list[int],
) -> None:
    result = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", 0),
        known_wrong=tuple(
            run(f"wrong-{index}", exit_code)
            for index, exit_code in enumerate(exit_codes)
        ),
    )

    assert result.to_json() == result.to_json()
    assert result.to_dict() == result.to_dict()


def test_empty_audit_cannot_claim_sufficient_strength() -> None:
    result = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", 0),
        known_wrong=(),
    )

    assert result.score == 0.0
    assert result.sufficient is False


@given(
    baseline_exit=st.integers(min_value=0, max_value=5),
    known_wrong_exits=st.lists(
        st.integers(min_value=0, max_value=5), min_size=0, max_size=10
    ),
)
def test_enforcement_succeeds_exactly_when_the_audit_is_sufficient(
    baseline_exit: int, known_wrong_exits: list[int]
) -> None:
    result = AuditResult(
        manifest="tlaw.toml",
        baseline=run("reference", baseline_exit),
        known_wrong=tuple(
            run(f"wrong-{index}", exit_code)
            for index, exit_code in enumerate(known_wrong_exits)
        ),
    )

    if result.sufficient:
        assert result.enforce() is result
    else:
        with pytest.raises(EnforcementError) as caught:
            result.enforce()
        assert caught.value.result is result
