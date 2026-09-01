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
