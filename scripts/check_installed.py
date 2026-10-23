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


def verify_mutation(report: dict) -> None:
    require(report["schema"] == "tlaw.mutation/v1", "missing mutation evidence")
    require(report["baseline"]["valid"] is True, "mutation baseline is invalid")
    [baseline] = report["baseline"]["obligations"]
    require(baseline["identity"] == LAW, "missing baseline law")
    require(baseline["status"] == "passed", "baseline law did not pass")
    require(report["canary"]["wired"] is True, "canary did not observe mutation")
    require(report["canary"]["exit_code"] == 1, "canary was not killed by a law")
    require(report["canary"]["timed_out"] is False, "canary unexpectedly timed out")
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
    print("installed gate: sampled, exhaustive 3/3, counterexample JSON, replay,")
    print(
        "installed gate: repaired replay rejected; canary and 1/1 exact mutant replay passed"
    )


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
