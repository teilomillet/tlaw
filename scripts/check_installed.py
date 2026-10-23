"""Exercise the installed wheel through the worked example, outside the checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Literal


ROOT = Path(__file__).resolve().parents[1]
LAW = "laws.py::increment_advances_once"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith(("TLAW_", "PYTEST_")) or name in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
        }:
            environment.pop(name)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    return environment


def run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    expected_exit: int = 0,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    require(
        result.returncode == expected_exit,
        f"{command!r}: expected exit {expected_exit}, got {result.returncode}\n"
        f"{result.stdout}",
    )
    return result.stdout


def read_check(path: Path, *, status: str, method: str) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    require(report["schema"] == "tlaw.check/v1", "missing check evidence")
    require(report["summary"]["status"] == status, "unexpected check status")
    require(report["summary"]["retained"] is True, "evidence was not retained")
    [obligation] = report["obligations"]
    require(obligation["identity"] == LAW, "unexpected law inventory")
    require(obligation["status"] == status, "unexpected obligation status")
    require(obligation["method"] == method, "unexpected checking method")
    return report


def verify_mutation(report: dict, *, method: str = "sampled") -> None:
    def verify_obligation(obligation: dict, *, status: str, label: str) -> None:
        require(obligation["identity"] == LAW, f"unexpected {label} law")
        require(obligation["status"] == status, f"unexpected {label} status")
        require(obligation.get("method") == method, f"unexpected {label} method")
        if method == "exhaustive":
            checked = 3 if status == "passed" else 1
            require(
                obligation.get("cases_checked") == checked
                and obligation.get("cases_total") == 3
                and obligation.get("exhausted") is (status == "passed")
                and obligation.get("list_bounds") == {},
                f"unexpected {label} exhaustive counts or bounds",
            )
        else:
            require(
                obligation.get("cases_checked") is None
                and obligation.get("cases_total") is None
                and obligation.get("exhausted") is False
                and obligation.get("list_bounds") == {},
                f"unexpected {label} sampled counts or bounds",
            )

    require(report["schema"] == "tlaw.mutation/v1", "missing mutation evidence")
    require(report.get("method") == method, "unexpected mutation method")
    require(
        report.get("max_cases") == (3 if method == "exhaustive" else None),
        "unexpected mutation case cap",
    )
    require(report["baseline"]["valid"] is True, "mutation baseline is invalid")
    [baseline] = report["baseline"]["obligations"]
    verify_obligation(baseline, status="passed", label="baseline")
    require(report["canary"]["wired"] is True, "canary did not observe mutation")
    require(report["canary"]["exit_code"] == 1, "canary was not killed by a law")
    require(report["canary"]["timed_out"] is False, "canary unexpectedly timed out")
    [canary] = report["canary"]["obligations"]
    verify_obligation(canary, status="failed", label="canary")
    summary = report["summary"]
    require(
        summary["runnable"] == summary["killed"] == 1
        and summary["survived"] == summary["unresolved_equivalent"] == 0
        and summary["invalid"] == summary["errors"] == 0
        and summary["has_errors"] is False,
        "expected exactly one killed mutant and no unresolved outcomes",
    )
    [mutant] = report["mutations"]
    require(mutant["outcome"] == "killed", "mutant was not killed")
    require(mutant["kill_kind"] == "observed", "expected an observed law failure")
    require(mutant["replay_method"] == "exact", "mutant kill lacks exact replay")
    require(mutant["replay_exit_code"] == 0, "mutant replay did not pass")
    require(mutant["replay_timed_out"] is False, "mutant replay timed out")
    failure = mutant["evidence"]
    require(failure["summary"]["status"] == "failed", "missing mutant failure")
    require(failure["summary"]["retained"] is True, "mutant failure not retained")
    [failed_obligation] = failure["obligations"]
    verify_obligation(failed_obligation, status="failed", label="mutant")
    [counterexample] = failure["failures"]
    require(counterexample["identity"] == LAW, "unexpected mutant failure")
    require(counterexample["capture_error"] is None, "mutant input not replayable")
    replay = mutant["replay_evidence"]
    require(replay["mode"] == "replay", "missing mutant replay evidence")
    require(replay["summary"]["status"] == "passed", "mutant replay incomplete")
    [obligation] = replay["obligations"]
    require(
        obligation["identity"] == LAW
        and obligation["method"] == "replay"
        and obligation["status"] == "passed"
        and obligation["cases_checked"] == obligation["cases_total"] == 1,
        "retained mutant input was not replayed exactly once",
    )


def verify_campaign_budget(
    report: dict, *, stage: Literal["plan", "blocked", "complete"]
) -> None:
    """Check the worked example's plan and each run-budget boundary."""
    limits = {"plan": 1, "blocked": 3, "complete": 4}
    require(report["schema"] == "tlaw.mutation/v1", "missing campaign evidence")
    require(
        report.get("method") == "exhaustive" and report.get("max_cases") == 3,
        "campaign does not use the requested finite domain",
    )
    require(
        report.get("plan")
        == {
            "generated_mutations": 1,
            "selected_mutations": 1,
            "excluded_mutations": 0,
            "runnable_mutations": 1,
            "law_cases": 1,
            "cases_per_run": 3,
            "min_runs": 3,
            "max_runs": 5,
            "max_case_attempts": 15,
        },
        "unexpected campaign plan",
    )
    require(report["baseline"]["valid"] is True, "campaign baseline is invalid")
    [baseline] = report["baseline"]["obligations"]
    require(
        baseline["identity"] == LAW
        and baseline["status"] == "passed"
        and baseline["method"] == "exhaustive"
        and baseline["cases_checked"] == baseline["cases_total"] == 3
        and baseline["exhausted"] is True
        and baseline["list_bounds"] == {},
        "campaign baseline did not exhaust 3/3 cases",
    )
    [mutant] = report["mutations"]
    require(
        report.get("work")
        == {
            "max_runs": limits[stage],
            "runs_started": limits[stage],
            "runs_remaining": 0,
            "stopped_before": (
                f"exact replay for {mutant['id']}" if stage == "blocked" else None
            ),
        },
        "unexpected campaign work accounting",
    )
    summary = report["summary"]
    require(report.get("plan_only") is (stage == "plan"), "wrong planning mode")
    require(
        summary.get("complete") is (stage == "complete")
        and summary.get("not_run") == (1 if stage == "plan" else 0),
        "unexpected campaign completion or unrun count",
    )
    if stage == "complete":
        verify_mutation(report, method="exhaustive")
        return
    require(
        summary["generated"] == 1
        and summary["killed"] == summary["runnable"] == 0
        and summary["survived"] == summary["unresolved_equivalent"] == 0
        and summary["invalid"] == 0
        and summary["errors"] == (1 if stage == "blocked" else 0)
        and summary["has_errors"] is (stage == "blocked"),
        "unfinished campaign claims a mutation outcome",
    )
    require(
        mutant["kill_kind"] is None
        and mutant["failed_tests"] == []
        and mutant["replay_evidence"] == {}
        and all(
            mutant[field] is None
            for field in (
                "replay_method",
                "replay_exit_code",
                "replay_timed_out",
                "replay_duration_seconds",
            )
        ),
        "unrun confirmation claims a kill or replay evidence",
    )
    if stage == "plan":
        require(report["canary"] is None, "plan unexpectedly ran its canary")
        require(
            mutant["outcome"] == "not-run"
            and mutant["exit_code"] is None
            and mutant["evidence"] == {},
            "plan unexpectedly ran its mutant",
        )
    else:
        require(report["canary"]["wired"] is True, "budgeted canary was not wired")
        require(
            mutant["outcome"] == "error" and "confirmation not run" in mutant["note"],
            "unconfirmed failure was not reported as an error",
        )
        failure = mutant["evidence"]
        [obligation] = failure["obligations"]
        require(
            failure["summary"]["status"] == "failed"
            and failure["summary"]["retained"] is True
            and obligation["identity"] == LAW
            and obligation["method"] == "exhaustive"
            and obligation["status"] == "failed"
            and obligation["cases_checked"] == 1
            and obligation["cases_total"] == 3
            and obligation["exhausted"] is False
            and obligation["list_bounds"] == {},
            "unconfirmed failure history was not retained",
        )
        [counterexample] = failure["failures"]
        require(
            counterexample["identity"] == LAW
            and counterexample["capture_error"] is None,
            "unconfirmed counterexample was not retained",
        )


def verify_selection_campaign(
    report: dict,
    *,
    inventory: tuple[str, ...],
    selected_id: str,
    complete: bool,
) -> None:
    """Verify one selected mutant without losing the full generated inventory."""
    require(report["schema"] == "tlaw.mutation/v1", "missing selection evidence")
    require(
        report.get("method") == "exhaustive" and report.get("max_cases") == 3,
        "selection does not use the requested finite domain",
    )
    require(
        len(inventory) == len(set(inventory)) == 2 and selected_id in inventory,
        "selection fixture requires two distinct current IDs",
    )
    mutations = report["mutations"]
    require(
        tuple(mutant["id"] for mutant in mutations) == inventory,
        "selection changed the generated inventory or order",
    )
    [selected] = [mutant for mutant in mutations if mutant["id"] == selected_id]
    [excluded] = [mutant for mutant in mutations if mutant["id"] != selected_id]
    require(
        selected.get("selected") is True
        and excluded.get("selected") is False
        and excluded["outcome"] == "excluded",
        "selection flags or excluded outcome are incorrect",
    )
    for mutant in [excluded] if complete else mutations:
        require(
            mutant["exit_code"] is None
            and mutant["timed_out"] is False
            and mutant["duration_seconds"] == 0
            and mutant["kill_kind"] is None
            and mutant["failed_tests"] == []
            and mutant["evidence"] == mutant["replay_evidence"] == {}
            and all(
                mutant[field] is None
                for field in (
                    "replay_method",
                    "replay_exit_code",
                    "replay_timed_out",
                    "replay_duration_seconds",
                )
            ),
            "untouched mutant claims execution or confirmation evidence",
        )
    require(
        report.get("plan")
        == {
            "generated_mutations": 2,
            "selected_mutations": 1,
            "excluded_mutations": 1,
            "runnable_mutations": 1,
            "law_cases": 1,
            "cases_per_run": 3,
            "min_runs": 3,
            "max_runs": 5,
            "max_case_attempts": 15,
        },
        "selection plan does not describe the requested scope",
    )
    limit = 4 if complete else 2
    require(
        report.get("work")
        == {
            "max_runs": limit,
            "runs_started": limit,
            "runs_remaining": 0,
            "stopped_before": None if complete else f"mutant {selected_id}",
        },
        "selection run accounting does not preserve the budget",
    )
    summary = report["summary"]
    require(
        summary.get("generated") == 2
        and summary.get("selected") == summary.get("excluded") == 1
        and summary.get("not_run") == (0 if complete else 1)
        and summary.get("selection_complete") is complete
        and summary.get("complete") is False
        and summary.get("has_errors") is (not complete)
        and summary["killed"] == summary["runnable"] == int(complete)
        and summary["survived"] == summary["unresolved_equivalent"] == 0
        and summary["invalid"] == summary["errors"] == 0,
        "selection summary confuses selected, excluded or unrun work",
    )
    require(report.get("plan_only") is False, "selection unexpectedly only planned")
    require(report["baseline"]["valid"] is True, "selection baseline is invalid")
    require(
        report["canary"]["wired"] is True
        and report["canary"]["exit_code"] == 1
        and report["canary"]["timed_out"] is False,
        "selection did not preserve its fresh canary",
    )
    for label, status, count in (("baseline", "passed", 3), ("canary", "failed", 1)):
        [obligation] = report[label]["obligations"]
        require(
            obligation["identity"] == LAW
            and obligation["method"] == "exhaustive"
            and obligation["status"] == status
            and obligation["cases_checked"] == count
            and obligation["cases_total"] == 3
            and obligation["exhausted"] is (status == "passed")
            and obligation["list_bounds"] == {},
            f"selection lost its {label} exhaustive evidence",
        )
    if complete:
        verify_mutation({**report, "mutations": [selected]}, method="exhaustive")
    else:
        require(selected["outcome"] == "not-run", "selected mutant unexpectedly ran")


def exercise(cli: Path, project: Path, environment: dict[str, str]) -> None:
    def tlaw(*arguments: str, expected_exit: int = 0) -> str:
        return run(
            [str(cli), *arguments],
            cwd=project,
            environment=environment,
            expected_exit=expected_exit,
        )

    tlaw("check", "laws.py", "--json", "passing.json")
    passing = read_check(project / "passing.json", status="passed", method="sampled")
    require(passing["failures"] == [], "passing code retained a failure")
    tlaw(
        "check",
        "laws.py",
        "--exhaustive",
        "--max-cases",
        "3",
        "--json",
        "exhaustive.json",
    )
    exhaustive = read_check(
        project / "exhaustive.json", status="passed", method="exhaustive"
    )
    [obligation] = exhaustive["obligations"]
    require(
        obligation["cases_checked"] == obligation["cases_total"] == 3
        and obligation["exhausted"] is True,
        "finite domain was not exhausted: expected 3/3 cases",
    )

    implementation = project / "implementation.py"
    original = implementation.read_bytes()
    implementation.write_bytes((project / "implementation_wrong.py").read_bytes())
    try:
        tlaw("check", "laws.py", "--json", "failure.json", expected_exit=1)
        failed = read_check(project / "failure.json", status="failed", method="sampled")
        [failure] = failed["failures"]
        require(failure["identity"] == LAW, "counterexample belongs to another law")
        require(failure["capture_error"] is None, "counterexample is not replayable")
        value = failure["inputs"]["value"]
        require(value["type"] == "int", "counterexample is not an encoded integer")
        require(0 <= int(value["value"]) <= 2, "counterexample is outside the domain")
        tlaw("check", "--replay", "failure.json")
    finally:
        implementation.write_bytes(original)

    output = tlaw("check", "--replay", "failure.json", expected_exit=1)
    require(
        "retained counterexample no longer fails" in output,
        "replay did not reject the repair",
    )
    tlaw("check", "laws.py")
    tlaw("mutate", "laws.py", "implementation.py", "--json", "mutation.json")
    verify_mutation(json.loads((project / "mutation.json").read_text(encoding="utf-8")))
    for stage, arguments, expected_exit in (
        ("plan", ["--plan", "--max-runs", "1"], 0),
        ("blocked", ["--max-runs", "3"], 2),
    ):
        report_name = f"campaign-{stage}.json"
        tlaw(
            "mutate",
            "laws.py",
            "implementation.py",
            "--exhaustive",
            "--max-cases",
            "3",
            *arguments,
            "--json",
            report_name,
            expected_exit=expected_exit,
        )
        verify_campaign_budget(
            json.loads((project / report_name).read_text(encoding="utf-8")),
            stage=stage,
        )
    tlaw(
        "mutate",
        "laws.py",
        "implementation.py",
        "--exhaustive",
        "--max-cases",
        "3",
        "--max-runs",
        "4",
        "--json",
        "mutation-exhaustive.json",
    )
    exhaustive_mutation = json.loads(
        (project / "mutation-exhaustive.json").read_text(encoding="utf-8")
    )
    verify_campaign_budget(exhaustive_mutation, stage="complete")
    selected_id = exhaustive_mutation["mutations"][0]["id"]
    # An unrelated function adds an excluded mutant while preserving the chosen ID.
    implementation.write_bytes(
        original + b"\n\ndef decrement(value: int) -> int:\n    return value - 1\n"
    )
    try:
        tlaw(
            "mutate",
            "laws.py",
            "implementation.py",
            "--plan",
            "--json",
            "inventory.json",
        )
        inventory_report = json.loads(
            (project / "inventory.json").read_text(encoding="utf-8")
        )
        inventory = tuple(mutant["id"] for mutant in inventory_report["mutations"])
        for complete, limit in ((False, 2), (True, 4)):
            name = f"selection-{limit}.json"
            tlaw(
                "mutate",
                "laws.py",
                "implementation.py",
                "--mutant",
                selected_id,
                "--mutant",
                selected_id,
                "--exhaustive",
                "--max-cases",
                "3",
                "--max-runs",
                str(limit),
                "--json",
                name,
                expected_exit=0 if complete else 2,
            )
            verify_selection_campaign(
                json.loads((project / name).read_text(encoding="utf-8")),
                inventory=inventory,
                selected_id=selected_id,
                complete=complete,
            )
    finally:
        implementation.write_bytes(original)
    print("installed gate: sampled, exhaustive 3/3, counterexample JSON, replay,")
    print(
        "installed gate: repaired replay rejected; sampled and exhaustive mutation "
        "with canary and exact replay passed"
    )
    print("installed gate: baseline-only plan and guarded confirmation budget passed")
    print("installed gate: exact ID selection preserves excluded and unrun outcomes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel", type=Path, help="test an already-built release wheel"
    )
    args = parser.parse_args()
    environment = clean_environment()
    expected_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    with tempfile.TemporaryDirectory(prefix="tlaw-installed-") as directory:
        workspace = Path(directory).resolve()
        require(
            not workspace.is_relative_to(ROOT), "temporary workspace is inside checkout"
        )
        wheel = args.wheel.resolve() if args.wheel is not None else None
        if wheel is None:
            run(
                ["uv", "build", "--out-dir", str(workspace / "dist")],
                cwd=ROOT,
                environment=environment,
            )
            [wheel] = (workspace / "dist").glob("*.whl")
        require(wheel.is_file(), f"wheel not found: {wheel}")
        requirements = workspace / "runtime.txt"
        run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=ROOT,
            environment=environment,
        )
        venv = workspace / "venv"
        run(
            ["uv", "venv", "--python", sys.executable, str(venv)],
            cwd=workspace,
            environment=environment,
        )
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "-r",
                str(requirements),
            ],
            cwd=workspace,
            environment=environment,
        )
        run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=workspace,
            environment=environment,
        )
        run(
            ["uv", "pip", "check", "--python", str(python)],
            cwd=workspace,
            environment=environment,
        )
        project = workspace / "example"
        shutil.copytree(
            ROOT / "examples" / "workflow",
            project,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # A source import or editable install must never satisfy this gate.
        identity = run(
            [
                str(python),
                "-I",
                "-c",
                """
import importlib.metadata
from pathlib import Path
import sys
import tlaw

origin = Path(tlaw.__file__).resolve()
if not origin.is_relative_to(Path(sys.prefix).resolve()):
    raise SystemExit(f"tlaw was imported outside the fresh environment: {origin}")
version = importlib.metadata.version("tlaw")
if version != sys.argv[1]:
    raise SystemExit(f"expected tlaw {sys.argv[1]}, installed {version}")
print(f"installed gate: tlaw {version} from {origin}")
""",
                expected_version,
            ],
            cwd=project,
            environment=environment,
        )
        print(identity.strip())
        cli = bin_dir / ("tlaw.exe" if os.name == "nt" else "tlaw")
        exercise(cli, project, environment)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"installed gate failed: {error}") from error
