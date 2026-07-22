from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from tlaw import ManifestError, audit, check, mutate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tlaw",
        description="Write and enforce executable behavioral laws for Python code.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check", help="run one human-authored laws.py file")
    check.add_argument("law_file", type=Path)
    check.add_argument(
        "--explain",
        action="store_true",
        help="show how inferred inputs are generated",
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        try:
            result = check(args.law_file, explain=args.explain)
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(result.output, end="" if result.output.endswith("\n") else "\n")
        return result.exit_code

    if args.command == "mutate":
        try:
            result = mutate(
                args.law_file,
                args.implementation,
                equivalents=args.equivalents,
            )
        except ManifestError as error:
            print(f"tlaw: {error}", file=sys.stderr)
            return 2
        print(result.render_text())
        if args.json_path is not None:
            args.json_path.write_text(result.to_json(), encoding="utf-8")
            print(f"JSON: {args.json_path}")
        # Exit-code contract, mirroring `tlaw audit`: 2 means the evidence
        # itself is broken (invalid baseline or harness errors), 1 means
        # unexplained survivors remain — a prompt for investigation, not
        # proof the laws are weak — and 0 means every runnable mutant was
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
        args.json_path.write_text(result.to_json(), encoding="utf-8")
        print(f"JSON: {args.json_path}")

    if result.has_errors:
        return 2
    return 0 if result.sufficient else 1
