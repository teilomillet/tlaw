from __future__ import annotations

from pathlib import Path

import pytest
import tlaw
from tlaw.book import Lawbook


def test_python_lawbook_is_an_executable_audit_specification(tmp_path: Path) -> None:
    (tmp_path / "implementations.py").write_text(
        """
class Correct:
    valid = True

class Wrong:
    valid = False
""".strip(),
        encoding="utf-8",
    )
    laws_path = tmp_path / "laws.py"
    laws_path.write_text(
        """
from implementations import Correct, Wrong
from tlaw.book import Lawbook
from tlaw.target import load_target

laws = Lawbook(__file__)
laws.baseline("correct", Correct)
laws.known_wrong("wrong", Wrong, breaks="The value must remain valid.")

def test_value_remains_valid():
    assert load_target().valid is True
""".strip(),
        encoding="utf-8",
    )

    result = tlaw.audit(laws_path)

    assert result.baseline_valid is True
    assert result.rejected_count == 1
    assert result.sufficient is True


def test_lawbook_rejects_local_implementations_that_workers_cannot_import(
    tmp_path: Path,
) -> None:
    laws = Lawbook(tmp_path / "laws.py")

    class LocalImplementation:
        pass

    try:
        laws.baseline("local", LocalImplementation)
    except tlaw.ManifestError as error:
        assert "importable module" in str(error)
    else:
        raise AssertionError("a local implementation cannot be a worker target")


@pytest.mark.parametrize(
    ("body", "count"),
    [
        ("answer = 42", 0),
        (
            "from tlaw.book import Lawbook\nfirst = Lawbook(__file__)\n"
            "second = Lawbook(__file__)",
            2,
        ),
    ],
)
def test_python_spec_fails_closed_without_exactly_one_lawbook(
    tmp_path: Path, body: str, count: int
) -> None:
    laws_path = tmp_path / "laws.py"
    laws_path.write_text(body, encoding="utf-8")

    with pytest.raises(tlaw.ManifestError, match=f"found {count}"):
        tlaw.audit(laws_path)
