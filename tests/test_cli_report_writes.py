"""CLI report-write failures must remain ordinary evidence errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tlaw.cli import main


@pytest.mark.parametrize(
    ("command", "runner"),
    [
        (["mutate", "laws.py", "implementation.py"], "tlaw.cli.mutate"),
        (["audit", "audit.py"], "tlaw.cli.audit"),
    ],
)
def test_json_write_errors_return_two_without_a_traceback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
    runner: str,
) -> None:
    result = SimpleNamespace(
        render_text=lambda: "evidence report",
        to_json=lambda: "{}\n",
        has_errors=False,
        survived_count=0,
        sufficient=True,
    )
    monkeypatch.setattr(runner, lambda *args, **kwargs: result)
    destination = tmp_path / "missing" / "report.json"

    exit_code = main([*command, "--json", str(destination)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert not destination.exists()
    assert "could not write JSON report" in captured.err
    assert "Traceback" not in captured.err


def test_unsupported_exhaustive_domain_is_reported_without_a_traceback(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        "import tlaw\n"
        "\n"
        "Reward = tlaw.number(0, 1)\n"
        "\n"
        "@tlaw.law\n"
        "def stays_in_range(reward: Reward) -> bool:\n"
        "    return 0 <= reward <= 1\n",
        encoding="utf-8",
    )

    exit_code = main(["check", str(law_file), "--exhaustive"])

    captured = capsys.readouterr()
    assert exit_code == 2
    # The refusal keeps its actionable sentence and exit code; the pytest
    # collection traceback no longer buries it.
    assert "exhaustive checking refused this domain" in captured.err
    assert "does not support number(0, 1)" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
