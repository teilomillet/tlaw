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


def test_folder_check_and_mutate_surface_exhaustive_domain_refusals_on_stderr(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    law_file = tmp_path / "laws.py"
    impl_file = tmp_path / "implementation.py"
    impl_file.write_text(
        "def clamp(x: float) -> float:\n    return max(0.0, min(1.0, x))\n",
        encoding="utf-8",
    )
    law_file.write_text(
        "import tlaw\n"
        "from implementation import clamp\n\n"
        "Reward = tlaw.number(0, 1)\n\n"
        "@tlaw.law\n"
        "def stays_in_range(reward: Reward) -> bool:\n"
        "    return 0.0 <= clamp(reward) <= 1.0\n",
        encoding="utf-8",
    )

    folder_exit = main(["check", str(tmp_path), "--exhaustive"])
    folder_captured = capsys.readouterr()
    assert folder_exit == 2
    assert "exhaustive checking refused this domain" in folder_captured.err
    assert "does not support number(0, 1)" in folder_captured.err
    assert "Traceback" not in folder_captured.out

    mutate_exit = main(["mutate", str(law_file), str(impl_file), "--exhaustive"])
    mutate_captured = capsys.readouterr()
    assert mutate_exit == 2
    assert "exhaustive checking refused this domain" in mutate_captured.err
    assert "does not support number(0, 1)" in mutate_captured.err


def test_exhaustive_folder_keeps_all_verdicts_and_locates_every_refusal(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = (
        ("a_refused_laws.py", "tlaw.number(0, 1)", "True", "ERROR"),
        ("b_refused_laws.py", "tlaw.number(2, 3)", "True", "ERROR"),
        ("c_failed_laws.py", "bool", "False", "FAIL"),
        ("d_passed_laws.py", "bool", "True", "PASS"),
    )
    for name, domain, result, _ in cases:
        (tmp_path / name).write_text(
            "import tlaw\n@tlaw.law\n"
            f"def example(value: {domain}):\n    return {result}\n",
            encoding="utf-8",
        )

    exit_code = main(["check", str(tmp_path), "--exhaustive"])

    captured = capsys.readouterr()
    assert exit_code == 2
    for name, _, _, verdict in cases:
        assert any(
            line.strip().startswith(verdict) and line.endswith(str(tmp_path / name))
            for line in captured.out.splitlines()
        ), captured.out
    assert "1 of 4 law file(s) passed" in captured.out
    for name, domain, _, _ in cases[:2]:
        assert (
            f"{tmp_path / name}: exhaustive checking does not support "
            f"{domain.removeprefix('tlaw.')}"
        ) in captured.err
    assert captured.err.count("exhaustive checking refused this domain") == 2
    assert "Traceback" not in captured.out + captured.err
