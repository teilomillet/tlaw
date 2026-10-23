"""Regression probes: ``tlaw check`` must not modify the checked directory.

A check used to leave two caches behind: ``__pycache__`` from importing the law
and its implementation, and ``.hypothesis`` from Hypothesis's home directory,
which defaults to the working directory. Only inferred laws force
``database=None``; explicit-strategy laws keep the example database enabled, so
the home directory itself has to move. The child environment is recorded by the
law through an inherited variable whose value points outside the checked tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tlaw


CACHE_NAMES = {"__pycache__", ".hypothesis", ".pytest_cache"}
ENVIRONMENT_RECEIPT = "TLAW_TEST_ENVIRONMENT_RECEIPT"

INFERRED_LAW = """\
import json
import os

import tlaw


@tlaw.law
def adding_zero_changes_nothing(value: int):
    with open(os.environ["TLAW_TEST_ENVIRONMENT_RECEIPT"], "w") as receipt:
        json.dump(
            {
                name: os.environ.get(name)
                for name in (
                    "PYTHONHASHSEED",
                    "PYTHONDONTWRITEBYTECODE",
                    "HYPOTHESIS_STORAGE_DIRECTORY",
                )
            },
            receipt,
        )
    return value + 0 == value
"""

EXPLICIT_LAW = """\
import json
import os

from hypothesis import strategies as st

import tlaw
from implementation import value


@tlaw.law(
    "the result is always below zero",
    candidate=st.integers(min_value=0, max_value=3),
)
def result_is_always_negative(candidate: int):
    with open(os.environ["TLAW_TEST_ENVIRONMENT_RECEIPT"], "w") as receipt:
        json.dump(
            {"HYPOTHESIS_STORAGE_DIRECTORY": os.environ.get(
                "HYPOTHESIS_STORAGE_DIRECTORY"
            )},
            receipt,
        )
    return value() + candidate < 0
"""


def _boundary(directory: Path) -> Path:
    directory.mkdir()
    (directory / "implementation.py").write_text(
        "def value():\n    return 0\n", encoding="utf-8"
    )
    return directory


def _write_law(directory: Path, law: str) -> Path:
    law_file = directory / "laws.py"
    law_file.write_text(law, encoding="utf-8")
    return law_file


def _cache_entries(directory: Path) -> set[str]:
    return {
        path.name
        for path in directory.rglob("*")
        if path.name in CACHE_NAMES or path.suffix == ".pyc"
    }


def _child_environment(receipt: Path) -> dict[str, str | None]:
    return json.loads(receipt.read_text(encoding="utf-8"))


def test_an_inferred_check_leaves_no_cache_in_the_checked_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A parent seed must not reach the child: exact counterexample replay
    # depends on the child's hashing being pinned.
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    boundary = _boundary(tmp_path / "boundary")
    receipt = tmp_path / "child-environment.json"
    monkeypatch.setenv(ENVIRONMENT_RECEIPT, str(receipt))

    result = tlaw.check(_write_law(boundary, INFERRED_LAW))

    assert result.exit_code == 0
    assert _cache_entries(boundary) == set()
    environment = _child_environment(receipt)
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    storage = environment["HYPOTHESIS_STORAGE_DIRECTORY"]
    assert storage is not None
    assert not Path(storage).is_relative_to(boundary)


def test_an_explicit_strategy_check_leaves_no_example_database_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit-strategy laws keep Hypothesis's example database enabled, so a
    # refuted law is the case that used to write .hypothesis/examples into the
    # checked project.
    boundary = _boundary(tmp_path / "boundary")
    receipt = tmp_path / "child-environment.json"
    monkeypatch.setenv(ENVIRONMENT_RECEIPT, str(receipt))

    result = tlaw.check(_write_law(boundary, EXPLICIT_LAW))

    assert result.exit_code == 1
    assert "law failed: the result is always below zero" in result.output
    assert _cache_entries(boundary) == set()
    storage = _child_environment(receipt)["HYPOTHESIS_STORAGE_DIRECTORY"]
    assert storage is not None
    assert not Path(storage).is_relative_to(boundary)
