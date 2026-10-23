"""Sampling recursion and exhaustive declarations retain separate guarantees."""

from __future__ import annotations

from pathlib import Path

import pytest

import tlaw


def _write_law(tmp_path: Path, domain: str) -> Path:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "import tlaw\n\n"
        "@dataclass\n"
        "class Node:\n"
        "    child: object\n\n"
        "Trees = tlaw.recursive(\n"
        "    bool, lambda child: tlaw.record(child=child), max_leaves=3\n"
        ")\n"
        f"Values = {domain}\n\n"
        "@tlaw.law\n"
        "def accepts(value: Values):\n"
        "    Path('called').touch()\n"
        "    return True\n",
        encoding="utf-8",
    )
    return law_file


@pytest.mark.parametrize(
    "domain",
    [
        "Trees",
        "tlaw.record(tree=Trees)",
        "tlaw.list_of(Trees, max_size=1)",
        "tlaw.record(Node, child=Trees)",
    ],
)
def test_recursive_domains_remain_available_to_sampled_checks(
    tmp_path: Path, domain: str
) -> None:
    result = tlaw.check(_write_law(tmp_path, domain))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "called").is_file()
    [obligation] = result.report["obligations"]
    assert obligation["method"] == "sampled"
    assert obligation["exhausted"] is False


@pytest.mark.parametrize(
    "domain",
    [
        "Trees",
        "tlaw.record(tree=Trees)",
        "tlaw.list_of(Trees, max_size=0)",
        "tlaw.record(trees=tlaw.list_of(Trees, max_size=0))",
        "tlaw.record(Node, child=bool)",
        "tlaw.list_of(tlaw.record(Node, child=bool), max_size=0)",
    ],
)
def test_exhaustive_refuses_recursion_and_constructors_before_any_body_runs(
    tmp_path: Path, domain: str
) -> None:
    result = tlaw.check(_write_law(tmp_path, domain), exhaustive=True)

    assert result.exit_code == 2, result.output
    assert result.report["summary"]["status"] == "invalid"
    assert result.report["failures"] == []
    assert "exhaustive checking does not support" in result.output
    assert not (tmp_path / "called").exists()


def test_finite_record_and_list_metadata_survives_recursive_composition_support(
    tmp_path: Path,
) -> None:
    result = tlaw.check(
        _write_law(
            tmp_path,
            "tlaw.record(active=bool, history=tlaw.list_of(bool, max_size=1))",
        ),
        exhaustive=True,
        max_cases=6,
    )

    assert result.exit_code == 0, result.output
    [obligation] = result.report["obligations"]
    assert obligation["cases_checked"] == obligation["cases_total"] == 6
    assert obligation["exhausted"] is True
    assert obligation["list_bounds"] == {
        "value.history": {"min_size": 0, "max_size": 1}
    }
