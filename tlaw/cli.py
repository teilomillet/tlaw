from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from tlaw import ManifestError, audit, check, check_folder, diff, mutate, replay


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tlaw",
        description="Write and enforce executable behavioral laws for Python code.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser(
        "check", help="run one law file, or every law file under a folder"
    )
    check.add_argument(
        "law_file",
        nargs="?",
        type=Path,
        metavar="LAW_FILE_OR_FOLDER",
    )
    check.add_argument(
        "--explain",
        action="store_true",
        help="show how inferred inputs are generated",
    )
    check_mode = check.add_mutually_exclusive_group()
    check_mode.add_argument(
        "--search",
        action="store_true",
        help="use a fresh recorded seed to search for new counterexamples",
    )
    check_mode.add_argument(
        "--exhaustive",
        action="store_true",
        help="enumerate every declared finite input combination, without sampling",
    )
    check.add_argument(
        "--max-cases",
        type=int,
        help="maximum combinations per exhaustive law (default: 1000)",
    )
    check_mode.add_argument(
        "--replay",
        type=Path,
        metavar="REPORT",
        help="rerun exactly the failures retained in a check report",
    )
    check.add_argument(
        "--seed",
        type=int,
        help="reuse a recorded non-negative seed with --search",
    )
    check.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        help="write a replayable tlaw.check/v1 evidence report",
    )
    law_diff = subcommands.add_parser(
        "diff",
        help="show static law changes between two Git revisions",
    )
    law_diff.add_argument(
        "revisions",
        metavar="BASE..HEAD",
        help="two-dot Git revision range to compare",
    )
    audit = subcommands.add_parser(
        "audit", help="audit one laws.py file or legacy TOML manifest"
    )
    audit.add_argument("specification", type=Path)
    audit.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        help="write the stable audit report to this JSON file",
    )
    mutate = subcommands.add_parser(
        "mutate",
        help="challenge one laws.py file with generated mutations of one module",
    )
    mutate.add_argument("law_file", type=Path)
    mutate.add_argument("implementation", type=Path)
    mutate.add_argument(
        "--mutant",
        dest="mutant_ids",
        action="append",
        metavar="ID",
        help="select an exact generated mutant ID; repeat to select more than one",
    )
    mutate.add_argument(
        "--plan",
        action="store_true",
        help="run one baseline and report planned work without canary or mutant runs",
    )
    mutate.add_argument(
        "--max-runs",
        type=int,
        help="total isolated pytest invocations, including baseline and confirmations",
    )
    mutate.add_argument(
        "--exhaustive",
        action="store_true",
        help="enumerate declared finite inputs for baseline, canary and mutants",
    )
    mutate.add_argument(
        "--max-cases",
        type=int,
        help="maximum combinations per exhaustive law case (default: 1000)",
    )
    mutate.add_argument(
        "--equivalents",
        type=Path,
        help="TOML ledger of mutation IDs accepted as unresolved equivalents",
    )
    mutate.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        help="write the stable mutation report to this JSON file",
    )
    return parser


def _write_report(path: Path, contents: str) -> None:
    try:
        path.write_text(contents, encoding="utf-8")
    except OSError as error:
        raise ManifestError(f"could not write JSON report {path}: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "diff":
        try:
            result = diff(args.revisions)
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(result.render_text())
        return result.exit_code

    if args.command == "check":
        if args.max_cases is not None and (not args.exhaustive or args.max_cases <= 0):
            print(
                "tlaw: --max-cases requires --exhaustive and a positive integer",
                file=sys.stderr,
            )
            return 2
        if args.law_file is not None and args.law_file.is_dir():
            # Folder mode discovers and runs every law file; the per-file
            # evidence flags do not apply, so they are refused rather than
            # silently ignored.
            per_file = []
            if args.search:
                per_file.append("--search")
            if args.replay is not None:
                per_file.append("--replay")
            if args.json_path is not None:
                per_file.append("--json")
            if args.seed is not None:
                per_file.append("--seed")
            if per_file:
                print(
                    "tlaw: checking a folder does not support "
                    f"{', '.join(per_file)}; those retain per-file evidence",
                    file=sys.stderr,
                )
                return 2
            try:
                folder = check_folder(
                    args.law_file,
                    explain=args.explain,
                    exhaustive=args.exhaustive,
                    max_cases=args.max_cases,
                )
            except ManifestError as error:
                print(f"tlaw: {error}", file=sys.stderr)
                return 2
            print(folder.output, end="")
            return folder.exit_code
        try:
            if args.replay is not None:
                if args.law_file is not None:
                    raise ManifestError(
                        "check --replay reads its law path from REPORT; "
                        "do not also pass a law file"
                    )
                if args.explain:
                    raise ManifestError(
                        "check --replay cannot be combined with --explain"
                    )
                if args.json_path is not None:
                    raise ManifestError("check --replay cannot be combined with --json")
                if args.seed is not None:
                    raise ManifestError("check --replay cannot be combined with --seed")
                result = replay(args.replay)
            else:
                if args.law_file is None:
                    raise ManifestError("check needs a law file or --replay REPORT")
                if args.seed is not None and not args.search:
                    raise ManifestError("check --seed requires --search")
                if args.seed is not None and args.seed < 0:
                    raise ManifestError("check --seed must be non-negative")
                result = check(
                    args.law_file,
                    explain=args.explain,
                    search=args.search,
                    seed=args.seed,
                    exhaustive=args.exhaustive,
                    max_cases=args.max_cases,
                )
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(result.output, end="" if result.output.endswith("\n") else "\n")
        if args.json_path is not None:
            if not result.report["summary"].get("retained", True):
                # The law's verdict stands and was printed above. What failed
                # is this request for replayable evidence, so the failure is
                # reported against the request rather than against the code.
                print(
                    "tlaw: JSON not written because this counterexample cannot "
                    "be retained for replay",
                    file=sys.stderr,
                )
                return 2
            if (
                result.report["summary"]["status"] == "invalid"
                and not result.report["obligations"]
            ):
                print(
                    "tlaw: JSON not written because the evidence is not replayable",
                    file=sys.stderr,
                )
            else:
                try:
                    _write_report(args.json_path, result.to_json())
                except ManifestError as error:
                    print(f"tlaw: {error}", file=sys.stderr)
                    return 2
                print(f"JSON: {args.json_path}")
                if result.report["failures"]:
                    print(f"Replay: tlaw check --replay {args.json_path}")
        elif args.search and result.report["failures"]:
            print("Retain this counterexample with --json REPORT for --replay.")
        return result.exit_code

    if args.command == "mutate":
        if args.max_runs is not None and args.max_runs <= 0:
            print("tlaw: mutate --max-runs must be a positive integer", file=sys.stderr)
            return 2
        if args.max_cases is not None and (not args.exhaustive or args.max_cases <= 0):
            print(
                "tlaw: mutate --max-cases requires --exhaustive and a positive integer",
                file=sys.stderr,
            )
            return 2
        try:
            result = mutate(
                args.law_file,
                args.implementation,
                equivalents=args.equivalents,
                plan_only=args.plan,
                max_runs=args.max_runs,
                mutant_ids=args.mutant_ids,
                exhaustive=args.exhaustive,
                max_cases=args.max_cases,
            )
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(result.render_text())
        if args.json_path is not None:
            try:
                _write_report(args.json_path, result.to_json())
            except ManifestError as error:
                print(f"tlaw: {error}", file=sys.stderr)
                return 2
            print(f"JSON: {args.json_path}")
        # Exit-code contract, mirroring `tlaw audit`: 2 means the evidence
        # is invalid or incomplete (including a run-budget stop), 1 means
        # unexplained survivors remain — a prompt for investigation, not
        # proof the laws are weak — and 0 means every runnable selected mutant was
        # killed or is a committed unresolved-equivalent candidate.
        if result.has_errors:
            return 2
        return 1 if result.survived_count > 0 else 0

    if args.command != "audit":
        raise AssertionError(f"unhandled command: {args.command}")
    try:
        result = audit(args.specification)
    except ManifestError as error:
        print(f"tlaw: {error}", file=sys.stderr)
        return 2

    print(result.render_text())
    if args.json_path is not None:
        try:
            _write_report(args.json_path, result.to_json())
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(f"JSON: {args.json_path}")

    if result.has_errors:
        return 2
    return 0 if result.sufficient else 1
