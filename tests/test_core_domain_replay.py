"""Core domain observations use ordinary inputs that survive exact replay."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import tlaw


CORE_LAWS = Path(__file__).resolve().parents[1] / "self_laws" / "domains_laws.py"


def _core_laws():
    spec = importlib.util.spec_from_file_location("core_domain_laws", CORE_LAWS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_schedule_repeats_exactly_for_the_same_seed() -> None:
    laws = _core_laws()
    domain = tlaw.integer(0, 100)

    assert laws._samples(domain, 73) == laws._samples(domain, 73)


def test_sample_schedule_varies_with_the_retained_seed() -> None:
    laws = _core_laws()
    domain = tlaw.integer(0, 100)
    schedules = [laws._samples(domain, seed) for seed in range(5)]

    assert all(len(schedule) == 2 for schedule in schedules)
    assert len({tuple(schedule) for schedule in schedules}) > 1
    assert any(any(value != 0 for value in schedule) for schedule in schedules)


def test_singleton_domains_need_only_one_observation() -> None:
    assert _core_laws()._samples(tlaw.one_of([7]), 73) == [7]


def test_wrong_domain_generator_is_retained_replayed_and_rejected_after_repair(
    tmp_path: Path,
) -> None:
    source = CORE_LAWS.read_text(encoding="utf-8")
    wrong_generator = """

_real_number = number

def number(low, high):
    domain = _real_number(low, high)
    return tlaw.Domain(domain.description, st.just(float(high) + 1.0))
"""
    law_file = tmp_path / "laws.py"
    law_file.write_text(source + wrong_generator, encoding="utf-8")

    checked = tlaw.check(law_file)

    assert checked.exit_code == 1, checked.output
    assert checked.report["summary"]["retained"] is True
    [failure] = checked.report["failures"]
    assert failure["name"] == "number_domains_generate_finite_values"
    assert set(failure["inputs"]) == {"low", "width", "draw_seed"}
    assert all(value["type"] == "int" for value in failure["inputs"].values())
    report = tmp_path / "failure.json"
    report.write_text(checked.to_json(), encoding="utf-8")

    reproduced = tlaw.replay(report)

    assert reproduced.exit_code == 0, reproduced.output
    assert "retained counterexample reproduced" in reproduced.output
    law_file.write_text(source, encoding="utf-8")

    repaired = tlaw.replay(report)

    assert repaired.exit_code == 1, repaired.output
    assert "retained counterexample no longer fails" in repaired.output
