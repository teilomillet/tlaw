from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest
import tlaw

from tlaw.cli import main


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "tlaw test")
    _git(repository, "config", "user.email", "tlaw@example.test")
    return repository


def _commit(repository: Path, message: str, **files: str) -> str:
    for name, source in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def test_diff_classifies_literal_domain_changes_without_executing_laws(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "feature/laws.py": """\
                from pathlib import Path
                import tlaw

                Path("historical-code-ran").write_text("unsafe")
                Values = tlaw.integer(0, 100)

                @tlaw.law
                def result_stays_bounded(value: Values):
                    return value <= 100
            """,
            "feature/implementation.py": "LIMIT = 100\n",
        },
    )
    narrowed = _commit(
        repository,
        "narrow the accepted behavior",
        **{
            "feature/laws.py": """\
                from pathlib import Path
                import tlaw

                Path("historical-code-ran").write_text("unsafe")
                Values = tlaw.integer(0, 10)

                @tlaw.law
                def result_stays_bounded(value: Values):
                    return value <= 100
            """,
            "feature/implementation.py": "LIMIT = 10\n",
        },
    )

    result = tlaw.diff(f"{base}..{narrowed}", repository=repository)

    assert result.exit_code == 1
    assert [change.identity for change in result.changes] == [
        "feature/laws.py::result_stays_bounded"
    ]
    assert result.changes[0].classifications == ("domain narrowed",)
    assert "feature/implementation.py" not in result.render_text()
    assert not (repository / "historical-code-ran").exists()

    widened = _commit(
        repository,
        "widen the accepted behavior",
        **{
            "feature/laws.py": """\
                from pathlib import Path
                import tlaw

                Path("historical-code-ran").write_text("unsafe")
                Values = tlaw.integer(-5, 20)

                @tlaw.law
                def result_stays_bounded(value: Values):
                    return value <= 100
            """,
        },
    )
    assert tlaw.diff(f"{narrowed}..{widened}", repository=repository).changes[
        0
    ].classifications == ("domain widened",)

    dynamic = _commit(
        repository,
        "make the domain dynamic",
        **{
            "feature/laws.py": """\
                from pathlib import Path
                import tlaw

                Path("historical-code-ran").write_text("unsafe")
                Values = tlaw.integer(0, choose_limit())

                @tlaw.law
                def result_stays_bounded(value: Values):
                    return value <= 100
            """,
        },
    )
    assert tlaw.diff(f"{widened}..{dynamic}", repository=repository).changes[
        0
    ].classifications == ("domain changed",)
    assert not (repository / "historical-code-ran").exists()


def test_diff_ignores_formatting_but_reports_body_and_description_changes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "laws.py": """\
                from tlaw import law

                @law("reversal is stable", values=list[int])
                def reversal_is_stable(values):
                    return list(reversed(list(reversed(values)))) == values
            """,
        },
    )
    formatting = _commit(
        repository,
        "format only",
        **{
            "laws.py": """\
                from tlaw import law


                @law(
                    "reversal is stable",
                    values=list[int],
                )
                def reversal_is_stable(values):
                    return (
                        list(reversed(list(reversed(values))))
                        == values
                    )
            """,
        },
    )

    unchanged = tlaw.diff(f"{base}..{formatting}", repository=repository)
    assert unchanged.exit_code == 0
    assert unchanged.changes == ()

    changed = _commit(
        repository,
        "change meaning",
        **{
            "laws.py": """\
                from tlaw import law

                @law("reversal preserves order", values=list[int])
                def reversal_is_stable(values):
                    return list(reversed(values)) == values
            """,
        },
    )
    result = tlaw.diff(f"{formatting}..{changed}", repository=repository)
    assert result.changes[0].classifications == (
        "description changed",
        "body changed",
    )


def test_diff_reports_added_and_removed_laws(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "laws.py": """\
                import tlaw

                @tlaw.law
                def old_law(value: int):
                    return value == value
            """,
        },
    )
    head = _commit(
        repository,
        "replace law",
        **{
            "laws.py": """\
                import tlaw

                @tlaw.law
                def new_law(value: int):
                    return value == value
            """,
        },
    )

    result = tlaw.diff(f"{base}..{head}", repository=repository)

    assert [(change.identity, change.classifications) for change in result.changes] == [
        ("laws.py::new_law", ("added",)),
        ("laws.py::old_law", ("removed",)),
    ]


def test_diff_recognizes_direct_authoring_imports(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    base = _commit(repository, "base", **{"placeholder.py": "VALUE = 1\n"})
    head = _commit(
        repository,
        "add laws",
        **{
            "direct_laws.py": """\
                from tlaw.authoring import law

                @law
                def direct_import(value: int):
                    return value == value
            """,
            "module_laws.py": """\
                import tlaw.authoring as authoring

                @authoring.law
                def module_import(value: int):
                    return value == value
            """,
        },
    )

    result = tlaw.diff(f"{base}..{head}", repository=repository)

    assert [change.identity for change in result.changes] == [
        "direct_laws.py::direct_import",
        "module_laws.py::module_import",
    ]


def test_diff_never_guesses_a_dynamic_list_bound(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "laws.py": """\
                import tlaw

                Values = tlaw.list_of(
                    tlaw.integer(low=0, high=10),
                    max_size=20,
                )

                @tlaw.law
                def values_fit(values: Values):
                    return len(values) <= 20
            """,
        },
    )
    head = _commit(
        repository,
        "dynamic bound",
        **{
            "laws.py": """\
                import tlaw

                Values = tlaw.list_of(
                    tlaw.integer(low=0, high=10),
                    max_size=choose_limit(),
                )

                @tlaw.law
                def values_fit(values: Values):
                    return len(values) <= 20
            """,
        },
    )

    result = tlaw.diff(f"{base}..{head}", repository=repository)

    assert result.changes[0].classifications == ("domain changed",)


def test_diff_makes_generation_and_decorators_loud(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "laws.py": """\
                import tlaw

                Values = tlaw.integer(0, 10)

                @tlaw.law
                def bounded(value: Values):
                    return value < 10
            """,
        },
    )
    explicit = _commit(
        repository,
        "change generation posture",
        **{
            "laws.py": """\
                import tlaw

                Values = tlaw.integer(0, 10)

                @tlaw.law("bounded", value=Values)
                def bounded(value):
                    return value < 10
            """,
        },
    )
    assert tlaw.diff(f"{base}..{explicit}", repository=repository).changes[
        0
    ].classifications == ("generation changed",)

    skipped = _commit(
        repository,
        "skip the law",
        **{
            "laws.py": """\
                import pytest
                import tlaw

                Values = tlaw.integer(0, 10)

                @pytest.mark.skip
                @tlaw.law("bounded", value=Values)
                def bounded(value):
                    return value < 10
            """,
        },
    )
    assert tlaw.diff(f"{explicit}..{skipped}", repository=repository).changes[
        0
    ].classifications == ("decorators changed",)


def test_diff_cli_has_distinct_change_no_change_and_error_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository(tmp_path)
    base = _commit(
        repository,
        "base",
        **{
            "laws.py": """\
                import tlaw

                @tlaw.law
                def identity(value: int):
                    return value == value
            """,
        },
    )
    head = _commit(
        repository,
        "change body",
        **{
            "laws.py": """\
                import tlaw

                @tlaw.law
                def identity(value: int):
                    return value + 0 == value
            """,
        },
    )
    monkeypatch.chdir(repository)

    assert main(["diff", f"{base}..{head}"]) == 1
    assert "laws.py::identity: body changed" in capsys.readouterr().out
    assert main(["diff", f"{head}..{head}"]) == 0
    assert "No law changes." in capsys.readouterr().out
    assert main(["diff", "not-a-range"]) == 2
    assert "tlaw:" in capsys.readouterr().err
