"""Laws for folder discovery: `tlaw check DIR/` over many law files."""

from __future__ import annotations

from pathlib import Path

import pytest

import tlaw
from tlaw.check import check_folder, discover_law_files
from tlaw.cli import main


PASS_LAW = """\
import tlaw


@tlaw.law
def zero_is_zero(value: int):
    return value * 0 == 0
"""

FAIL_LAW = """\
import tlaw


@tlaw.law
def integers_stay_small(value: int):
    return value < 10
"""


def _feature(root: Path, name: str, body: str) -> Path:
    folder = root / name
    folder.mkdir()
    laws = folder / "laws.py"
    laws.write_text(body, encoding="utf-8")
    return laws


def test_discovery_finds_laws_py_and_split_law_files(tmp_path: Path) -> None:
    _feature(tmp_path, "alpha", PASS_LAW)
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "core_laws.py").write_text(PASS_LAW, encoding="utf-8")
    (tmp_path / "beta" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "beta" / "__pycache__").mkdir()
    (tmp_path / "beta" / "__pycache__" / "laws.py").write_text("", encoding="utf-8")

    found = discover_law_files(tmp_path)

    names = [path.name for path in found]
    assert names == ["laws.py", "core_laws.py"] or names == [
        "core_laws.py",
        "laws.py",
    ]
    assert all("__pycache__" not in path.parts for path in found)
    # Deterministic order.
    assert found == discover_law_files(tmp_path)


def test_a_folder_passes_only_when_every_file_passes(tmp_path: Path) -> None:
    _feature(tmp_path, "good", PASS_LAW)
    _feature(tmp_path, "bad", FAIL_LAW)

    result = check_folder(tmp_path)

    assert result.passed is False
    assert result.exit_code == 1
    assert "1 of 2 law file(s) passed" in result.output
    assert "FAIL" in result.output


def test_an_all_passing_folder_passes(tmp_path: Path) -> None:
    _feature(tmp_path, "one", PASS_LAW)
    _feature(tmp_path, "two", PASS_LAW)

    result = check_folder(tmp_path)

    assert result.passed is True
    assert result.exit_code == 0


def test_an_explicitly_selected_hidden_folder_is_checked(tmp_path: Path) -> None:
    hidden = tmp_path / ".contracts"
    hidden.mkdir()
    laws = hidden / "laws.py"
    laws.write_text(PASS_LAW, encoding="utf-8")

    assert discover_law_files(hidden) == (laws,)
    assert check_folder(hidden).passed is True


def test_an_empty_folder_is_refused_not_silently_passed(tmp_path: Path) -> None:
    with pytest.raises(tlaw.ManifestError):
        check_folder(tmp_path)


def test_the_cli_checks_a_folder_and_returns_the_worst_exit(tmp_path: Path) -> None:
    _feature(tmp_path, "good", PASS_LAW)
    _feature(tmp_path, "bad", FAIL_LAW)

    assert main(["check", str(tmp_path)]) == 1

    good_only = tmp_path / "good"
    assert main(["check", str(good_only)]) == 0


def test_folder_mode_refuses_per_file_evidence_flags(tmp_path: Path) -> None:
    _feature(tmp_path, "one", PASS_LAW)

    # --search, --json, --seed retain per-file evidence and have no folder
    # meaning; refused loudly rather than ignored.
    assert main(["check", str(tmp_path), "--search"]) == 2
    assert main(["check", str(tmp_path), "--json", str(tmp_path / "r.json")]) == 2
