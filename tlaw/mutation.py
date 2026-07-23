"""Generated-mutation evidence: challenges the law author did not choose.

What
----
This module generates small, deterministic, single-site mutations of one
implementation module, runs the unchanged laws against every mutant, and
reports which mutants the laws kill. It is the engine described in
``docs/mutation-evidence.md``: the counterweight for the loose authoring
form, built before any derived confidence view.

Why
---
The beginner interface makes weak laws cheap to write. The declared
known-wrong audit is self-graded: whoever writes the wrong implementations
controls the verdict. Generated mutations are challenges nobody selected,
so a kill is evidence the laws constrain behavior rather than evidence the
author chose beatable opponents.

How: the three evidence rules this module encodes
-------------------------------------------------
1. **Attribution by difference from baseline.** The baseline run must pass
   cleanly before any mutant runs. After that, an ordinary pytest failure
   under a mutant — a ``False`` comparison *or* an exception raised by the
   mutated implementation — is a behavioral difference and counts as a
   kill. ``error`` is reserved for outcomes the harness cannot attribute
   to the mutation. A mutant that breaks collection could not be loaded at
   all, so it is ``invalid``: never a kill, never a law success.

2. **A calibrated timeout is a kill.** An infinite loop is behavior, not
   infrastructure. The per-mutant budget is derived from the measured
   baseline duration (``max(floor, multiplier * baseline)``), and that
   calibration is recorded in the report. Only a timeout without a
   measured baseline would be an error; this engine always measures.

3. **A kill that does not replay is an error, not a kill.** Every kill is
   rerun once in a fresh workspace. Laws use deterministic generation by
   default, so a genuine kill reproduces; one that does not is recorded as
   an error rather than inflating strength. This is the only place tlaw
   ever downgrades a failure, and it downgrades toward claiming less.

4. **The canary must die before anything is scored.** Before the first
   real mutant runs, a poisoned copy of the implementation — every
   function raises — is run against the laws. If the laws still pass,
   they never observed the boundary module (typically: they import an
   installed package instead of the sibling file), every mutant would
   falsely "survive", and the report declares INVALID EVIDENCE instead.
   This is a default with no opt-out, because the person it protects is
   the one who has never heard of it.

Mutation identity is content-based, never line-based: the ID hashes the
module name, the enclosing function's qualified name, the structural path
of the mutated node *within that function*, the operator, and the
before/after snippets. Edits elsewhere in the file therefore never change
an ID, which is what lets the equivalence ledger survive unrelated
refactors. The ledger itself is ordinary committed data — marking a
survivor as equivalent is a trust decision, so stale entries are reported
instead of being silently dropped.

The strength statement stays narrow: "Killed X of Y runnable generated
mutations." It is never rendered as a percentage of correctness.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .audit import TARGET_ENV, ManifestError, _failed_tests


class MutationOutcome(Enum):
    """The five honest outcomes from docs/mutation-evidence.md.

    Invalid and error are never kills; an unresolved equivalent is a
    survivor with a committed explanation, still visible in every report.
    """

    KILLED = "killed"
    SURVIVED = "survived"
    INVALID = "invalid"
    ERROR = "error"
    UNRESOLVED_EQUIVALENT = "unresolved-equivalent"


# Why these operators: docs/mutation-evidence.md declares the first
# mutation boundary as small, understandable, single-site changes whose
# diff a reviewer can read in one line. Each mutant changes exactly one
# AST node so every kill or survival is attributable to one change.
_ARITHMETIC_SWAPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
}
_COMPARISON_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}

# One structural step: (field name, index into a list field or None).
_PathStep = tuple[str, int | None]
_Path = tuple[_PathStep, ...]


@dataclass(frozen=True)
class Mutation:
    """One generated single-site change, identified by content, not lines.

    ``mutation_id`` must stay stable across edits elsewhere in the module:
    the equivalence ledger and survivor tracking reference these IDs from
    ordinary commits, and line-based IDs would rot on every unrelated
    change. See ``_mutation_id`` for exactly what the ID commits to.
    """

    mutation_id: str
    operator: str
    qualname: str
    description: str
    diff: str
    mutated_source: str = field(repr=False)


@dataclass(frozen=True)
class _Site:
    """Where and how to apply one mutation to a freshly parsed tree.

    ``absolute_path`` locates the node in this exact source snapshot and
    is deliberately excluded from the mutation ID; ``relative_path`` is
    anchored to the enclosing function and is part of the ID.
    """

    absolute_path: _Path
    relative_path: _Path
    qualname: str
    operator: str
    build_replacement: Callable[[ast.AST], ast.AST]


def _path_text(path: _Path) -> str:
    steps = []
    for field_name, index in path:
        if index is None:
            steps.append(field_name)
        else:
            steps.append(f"{field_name}[{index}]")
    return ".".join(steps)


def _mutation_id(
    module_name: str,
    qualname: str,
    relative_path: _Path,
    operator: str,
    original: str,
    replacement: str,
) -> str:
    """Hash the mutation's content into a stable 12-character ID.

    The ID commits to: which module, which function, where inside that
    function, which operator, and the exact before/after code. It does
    not commit to line numbers or to the function's position in the
    module, so adding or moving unrelated code never changes an ID.
    Changing the mutated function itself does change IDs — correctly,
    because those mutants are of a different program.
    """
    payload = "\n".join(
        (
            module_name,
            qualname,
            _path_text(relative_path),
            operator,
            original,
            replacement,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _sites_for_node(
    node: ast.AST,
) -> list[tuple[str, _Path, Callable[[ast.AST], ast.AST]]]:
    """Return (operator, path suffix, replacement builder) for one node.

    Each builder receives the freshly located node from a new parse of the
    original source and returns a new node; it never mutates its input,
    so generating one mutant cannot contaminate another. The path suffix
    lets an operator address a child of the visited node — negation
    addresses the ``test`` expression of an ``if``/``while`` so the mutant
    stays a single-node change with a one-line description.
    """
    found: list[tuple[str, _Path, Callable[[ast.AST], ast.AST]]] = []

    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC_SWAPS:
        swap = _ARITHMETIC_SWAPS[type(node.op)]

        def replace_operator(
            current: ast.AST, new_op: type[ast.operator] = swap
        ) -> ast.AST:
            assert isinstance(current, ast.BinOp)
            return ast.BinOp(left=current.left, op=new_op(), right=current.right)

        found.append(("replace-arithmetic-operator", (), replace_operator))

    if isinstance(node, ast.Compare):
        for position, comparison in enumerate(node.ops):
            if type(comparison) not in _COMPARISON_SWAPS:
                continue
            swap_comparison = _COMPARISON_SWAPS[type(comparison)]

            def alter_boundary(
                current: ast.AST,
                index: int = position,
                new_op: type[ast.cmpop] = swap_comparison,
            ) -> ast.AST:
                assert isinstance(current, ast.Compare)
                operations = list(current.ops)
                operations[index] = new_op()
                return ast.Compare(
                    left=current.left,
                    ops=operations,
                    comparators=current.comparators,
                )

            found.append(("alter-comparison-boundary", (), alter_boundary))

    if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
        value = node.value.value
        # bool is checked before int because bool subclasses int.
        if isinstance(value, bool):

            def flip_bool(current: ast.AST) -> ast.AST:
                assert isinstance(current, ast.Return)
                assert isinstance(current.value, ast.Constant)
                return ast.Return(value=ast.Constant(value=not current.value.value))

            found.append(("replace-returned-constant", (), flip_bool))
        elif isinstance(value, int | float):

            def nudge_constant(current: ast.AST) -> ast.AST:
                assert isinstance(current, ast.Return)
                assert isinstance(current.value, ast.Constant)
                return ast.Return(value=ast.Constant(value=current.value.value + 1))

            found.append(("replace-returned-constant", (), nudge_constant))

    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):

        def skip_call(current: ast.AST) -> ast.AST:
            return ast.Pass()

        found.append(("skip-call-statement", (), skip_call))

    if isinstance(node, ast.If | ast.While):
        # The site addresses the test expression, not the whole statement:
        # the smallest change that flips the branch, one line in the diff.
        def negate_condition(current: ast.AST) -> ast.AST:
            assert isinstance(current, ast.expr)
            return ast.UnaryOp(op=ast.Not(), operand=current)

        found.append(("negate-condition", (("test", None),), negate_condition))

    return found


def _collect_sites(tree: ast.Module) -> list[_Site]:
    """Collect every mutation site, anchored to its enclosing function.

    Only code inside function bodies is mutated. Module-level statements
    run at import time, where a change surfaces as a load failure rather
    than as checkable behavior, and function anchoring is what gives
    mutation IDs their stability. Nested functions and methods are
    visited under their own qualified names.
    """
    sites: list[_Site] = []

    def visit_inside_function(
        node: ast.AST, absolute: _Path, relative: _Path, qualname: str
    ) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            walk_function(node, absolute, f"{qualname}.{node.name}")
            return
        if isinstance(node, ast.ClassDef):
            walk_scope(node, absolute, f"{qualname}.{node.name}.")
            return
        for operator, suffix, build in _sites_for_node(node):
            sites.append(
                _Site(
                    absolute_path=absolute + suffix,
                    relative_path=relative + suffix,
                    qualname=qualname,
                    operator=operator,
                    build_replacement=build,
                )
            )
        for field_name, value in ast.iter_fields(node):
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        step: _PathStep = (field_name, index)
                        visit_inside_function(
                            item, absolute + (step,), relative + (step,), qualname
                        )
            elif isinstance(value, ast.AST):
                step = (field_name, None)
                visit_inside_function(
                    value, absolute + (step,), relative + (step,), qualname
                )

    def walk_function(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        absolute: _Path,
        qualname: str,
    ) -> None:
        # Only the body is walked: decorators and default values execute
        # in the enclosing scope at definition time, not in the function.
        for index, statement in enumerate(function.body):
            step: _PathStep = ("body", index)
            visit_inside_function(statement, absolute + (step,), (step,), qualname)

    def walk_scope(
        scope: ast.Module | ast.ClassDef, absolute: _Path, prefix: str
    ) -> None:
        for index, statement in enumerate(scope.body):
            step: _PathStep = ("body", index)
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                walk_function(
                    statement, absolute + (step,), f"{prefix}{statement.name}"
                )
            elif isinstance(statement, ast.ClassDef):
                walk_scope(statement, absolute + (step,), f"{prefix}{statement.name}.")

    walk_scope(tree, (), "")
    return sites


def _resolve(tree: ast.AST, path: _Path) -> ast.AST:
    node: Any = tree
    for field_name, index in path:
        value = getattr(node, field_name)
        node = value[index] if index is not None else value
    return node


def _assign(tree: ast.AST, path: _Path, replacement: ast.AST) -> None:
    parent = _resolve(tree, path[:-1])
    field_name, index = path[-1]
    if index is None:
        setattr(parent, field_name, replacement)
    else:
        getattr(parent, field_name)[index] = replacement


def generate_mutations(source: str, module_name: str) -> tuple[Mutation, ...]:
    """Generate every single-site mutation of one module's functions.

    Deterministic by construction: the same source always yields the same
    mutations in the same order, with the same IDs and the same readable
    diffs. Diffs are computed against normalized (re-unparsed) source so
    they show exactly the one mutated site and no formatting noise.
    """
    tree = ast.parse(source)
    normalized = ast.unparse(tree)
    mutations: dict[str, Mutation] = {}

    for site in _collect_sites(tree):
        fresh_tree = ast.parse(source)
        target = _resolve(fresh_tree, site.absolute_path)
        replacement = site.build_replacement(target)
        original_snippet = ast.unparse(target)
        replacement_snippet = ast.unparse(replacement)
        _assign(fresh_tree, site.absolute_path, replacement)
        ast.fix_missing_locations(fresh_tree)
        mutated_source = ast.unparse(fresh_tree)

        mutation_id = _mutation_id(
            module_name,
            site.qualname,
            site.relative_path,
            site.operator,
            original_snippet,
            replacement_snippet,
        )
        diff = "\n".join(
            difflib.unified_diff(
                normalized.splitlines(),
                mutated_source.splitlines(),
                fromfile=f"{module_name}.py",
                tofile=f"{module_name}.py mutant {mutation_id}",
                lineterm="",
            )
        )
        # On an ID collision the later site wins: identical IDs can only
        # come from a redefinition of an identical function, where only
        # the last definition is live at runtime.
        mutations[mutation_id] = Mutation(
            mutation_id=mutation_id,
            operator=site.operator,
            qualname=site.qualname,
            description=(
                f"{site.qualname}: replace {original_snippet}"
                f" with {replacement_snippet}"
            ),
            diff=diff,
            mutated_source=mutated_source,
        )

    return tuple(mutations.values())


def poison_source(source: str) -> str:
    """Replace every function and method body with an immediate raise.

    This is the canary implementation. Its purpose is not to test the
    laws — it is to test the *wiring*: if the laws still pass when every
    function in the module raises, the laws never observed this module at
    all (the usual cause: laws import an installed package instead of the
    sibling module, so the mutated copy in the boundary never takes
    effect). In that situation every real mutant would "survive" and the
    report would look like weak laws when the truth is unwired evidence.

    Module-level statements, imports, decorators, and signatures are kept
    so the poisoned module still imports; only bodies are replaced. A
    module whose import itself executes its own functions will fail to
    collect under poison — that reports as a canary error, which is the
    conservative direction: tlaw says "cannot verify wiring" rather than
    guessing.
    """
    tree = ast.parse(source)

    poison = ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="RuntimeError", ctx=ast.Load()),
            args=[
                ast.Constant(
                    value=(
                        "tlaw canary: this implementation is poisoned; "
                        "a law that still passes never observed this module"
                    )
                )
            ],
            keywords=[],
        ),
        cause=None,
    )

    class _Poison(ast.NodeTransformer):
        def _replace_body(
            self, node: ast.FunctionDef | ast.AsyncFunctionDef
        ) -> ast.AST:
            self.generic_visit(node)  # poison nested definitions first
            node.body = [poison]
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            return self._replace_body(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            return self._replace_body(node)

    poisoned = _Poison().visit(tree)
    ast.fix_missing_locations(poisoned)
    return ast.unparse(poisoned)


@dataclass(frozen=True)
class CanaryRun:
    """The wiring check that runs before any mutant is scored.

    ``wired`` is the verdict:

    - ``True`` — the poisoned implementation was rejected (a law failed,
      or the run diverged from the passing baseline), so the laws
      demonstrably observe the mutated module and mutant outcomes are
      attributable;
    - ``False`` — the laws passed against a module in which every
      function raises: they never observed it, so every mutant outcome
      would be meaningless and the whole report is invalid evidence; and
    - ``None`` — the harness could not evaluate the canary, so wiring is
      unverified and tlaw refuses to score rather than guess.
    """

    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    note: str
    output: str = field(repr=False, default="")

    @property
    def wired(self) -> bool | None:
        if self.timed_out:
            # The baseline finished and the canary did not: the laws
            # observed a behavioral difference, which is all the canary
            # needs to prove.
            return True
        if self.exit_code == 1:
            return True
        if self.exit_code == 0:
            return False
        return None


def classify_mutant_exit(
    exit_code: int, *, timed_out: bool
) -> tuple[MutationOutcome, str]:
    """Map one mutant run to an outcome under attribution-by-baseline.

    This function is only called when the baseline already passed cleanly
    on the same laws with the same deterministic examples, so:

    - a timeout is a kill, because the budget was calibrated against the
      measured baseline and an infinite loop is behavior (rule 2);
    - pytest exit 1 is a kill, whether the law compared ``False`` or the
      mutated implementation raised — both are behavioral differences the
      baseline did not show (rule 1);
    - pytest exit 2 means collection failed, and since the baseline
      collected these same laws, the mutant could not be loaded: invalid,
      never a kill and never a law success; and
    - every other exit is a harness problem the mutation cannot be
      blamed or credited for: error.
    """
    if timed_out:
        return MutationOutcome.KILLED, "calibrated timeout"
    if exit_code == 0:
        return MutationOutcome.SURVIVED, "all laws passed"
    if exit_code == 1:
        return MutationOutcome.KILLED, "law failure or implementation exception"
    if exit_code == 2:
        return MutationOutcome.INVALID, "mutant could not be collected or loaded"
    return MutationOutcome.ERROR, f"harness exit code {exit_code}"


def load_equivalence_ledger(path: str | Path) -> dict[str, str]:
    """Load the committed equivalence ledger: mutation ID -> reason.

    The ledger is ordinary repository data because marking a survivor as
    equivalent is a trust decision: committed means accepted, and changes
    to this file belong in the same review scope as law changes. Format:

        version = 1

        [equivalent.<mutation_id>]
        reason = "why this mutant cannot be observed"
    """
    ledger_path = Path(path).resolve()
    try:
        with ledger_path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ManifestError(f"cannot read {ledger_path}: {error}") from error

    if data.get("version") != 1:
        raise ManifestError("equivalence ledger version must be 1")
    table = data.get("equivalent", {})
    if not isinstance(table, dict):
        raise ManifestError("equivalent must be a table of mutation IDs")
    ledger: dict[str, str] = {}
    for mutation_id, entry in table.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("reason"), str):
            raise ManifestError(
                f"equivalent.{mutation_id} must declare a reason string"
            )
        reason = entry["reason"].strip()
        if not reason:
            raise ManifestError(f"equivalent.{mutation_id} reason cannot be empty")
        ledger[mutation_id] = reason
    return ledger


@dataclass(frozen=True)
class BaselineRun:
    """The unmutated run every attribution decision is anchored to."""

    exit_code: int
    duration_seconds: float
    timed_out: bool
    output: str = field(repr=False, default="")
    command: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class MutantRun:
    """One mutant's final outcome plus the evidence behind it.

    ``exit_code`` is None when the mutant never ran (it failed to compile,
    or the baseline was invalid). ``failed_tests`` names the exact laws
    that rejected the mutant, so a law change can later say which
    survivors it kills.
    """

    mutation: Mutation
    outcome: MutationOutcome
    note: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    failed_tests: tuple[str, ...] = ()
    output: str = field(repr=False, default="")


def apply_equivalence_ledger(
    runs: tuple[MutantRun, ...], ledger: dict[str, str]
) -> tuple[tuple[MutantRun, ...], tuple[tuple[str, str], ...]]:
    """Reclassify surviving mutants the ledger explains; report the rest.

    Only a SURVIVED mutant can become UNRESOLVED_EQUIVALENT: the ledger
    explains survivors, it cannot overrule evidence. An entry whose mutant
    was killed, was invalid, errored, or does not exist is *stale* and is
    returned for the report — a ledger claim contradicted by evidence must
    stay visible, because quietly honoring or dropping it would be the new
    way to game the score. No run is ever removed: equivalents stay in
    every report and in the runnable denominator.
    """
    outcomes = {run.mutation.mutation_id: run.outcome for run in runs}
    updated: list[MutantRun] = []
    for run in runs:
        reason = ledger.get(run.mutation.mutation_id)
        if reason is not None and run.outcome is MutationOutcome.SURVIVED:
            updated.append(
                replace(
                    run,
                    outcome=MutationOutcome.UNRESOLVED_EQUIVALENT,
                    note=f"declared equivalent: {reason}",
                )
            )
        else:
            updated.append(run)

    stale: list[tuple[str, str]] = []
    for mutation_id, reason in ledger.items():
        outcome = outcomes.get(mutation_id)
        if outcome is None:
            stale.append((mutation_id, "no generated mutation has this ID"))
        elif outcome is not MutationOutcome.SURVIVED:
            stale.append(
                (mutation_id, f"contradicted by evidence: mutant was {outcome.value}")
            )
    return tuple(updated), tuple(stale)


@dataclass(frozen=True)
class MutationResult:
    """The full mutation report: every mutant visible, no percentage.

    The strength statement is deliberately narrow — "Killed X of Y
    runnable generated mutations" — and the renderers never compress it
    into a correctness or coverage percentage, because the runnable set
    is only the challenges this engine generated, not the space of
    possible mistakes.
    """

    laws: str
    implementation: str
    baseline: BaselineRun
    runs: tuple[MutantRun, ...]
    # Which shape was used and what the module is called inside it. A
    # reader must be able to tell a package run from a flat one without
    # inferring it from paths, because the two isolate differently.
    module_name: str = ""
    boundary_kind: str = "directory"
    stale_ledger_entries: tuple[tuple[str, str], ...] = ()
    budget_seconds: float = 0.0
    timeout_multiplier: float = 0.0
    timeout_floor_seconds: float = 0.0
    # None only when the canary never ran: no mutations to score, or the
    # baseline was already invalid. When mutants were scored, the canary
    # ran first — it is a default, not an option, so a user who has never
    # heard of it is still protected from unwired evidence.
    canary: CanaryRun | None = None

    def _count(self, outcome: MutationOutcome) -> int:
        return sum(run.outcome is outcome for run in self.runs)

    @property
    def baseline_valid(self) -> bool:
        return self.baseline.valid

    @property
    def killed_count(self) -> int:
        return self._count(MutationOutcome.KILLED)

    @property
    def survived_count(self) -> int:
        return self._count(MutationOutcome.SURVIVED)

    @property
    def unresolved_equivalent_count(self) -> int:
        return self._count(MutationOutcome.UNRESOLVED_EQUIVALENT)

    @property
    def invalid_count(self) -> int:
        return self._count(MutationOutcome.INVALID)

    @property
    def error_count(self) -> int:
        return self._count(MutationOutcome.ERROR)

    @property
    def runnable_total(self) -> int:
        # Invalid and error runs are excluded: they produced no evidence
        # about the laws, in either direction.
        return (
            self.killed_count + self.survived_count + self.unresolved_equivalent_count
        )

    @property
    def has_errors(self) -> bool:
        canary_failed = self.canary is not None and self.canary.wired is not True
        return (not self.baseline_valid) or canary_failed or self.error_count > 0

    @property
    def statement(self) -> str:
        return (
            f"Killed {self.killed_count} of {self.runnable_total} "
            "runnable generated mutations."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "tlaw.mutation/v1",
            "laws": self.laws,
            "implementation": self.implementation,
            "boundary": {"kind": self.boundary_kind, "module": self.module_name},
            "baseline": {
                "exit_code": self.baseline.exit_code,
                "duration_seconds": self.baseline.duration_seconds,
                "timed_out": self.baseline.timed_out,
                "valid": self.baseline_valid,
            },
            "budget": {
                "seconds": self.budget_seconds,
                "multiplier": self.timeout_multiplier,
                "floor_seconds": self.timeout_floor_seconds,
            },
            "canary": (
                None
                if self.canary is None
                else {
                    "exit_code": self.canary.exit_code,
                    "timed_out": self.canary.timed_out,
                    "duration_seconds": self.canary.duration_seconds,
                    "wired": self.canary.wired,
                    "note": self.canary.note,
                }
            ),
            "mutations": [
                {
                    "id": run.mutation.mutation_id,
                    "operator": run.mutation.operator,
                    "qualname": run.mutation.qualname,
                    "description": run.mutation.description,
                    "outcome": run.outcome.value,
                    "note": run.note,
                    "exit_code": run.exit_code,
                    "timed_out": run.timed_out,
                    "duration_seconds": run.duration_seconds,
                    "failed_tests": list(run.failed_tests),
                    "diff": run.mutation.diff,
                }
                for run in self.runs
            ],
            "stale_equivalence_entries": [
                {"id": mutation_id, "note": note}
                for mutation_id, note in self.stale_ledger_entries
            ],
            "summary": {
                "killed": self.killed_count,
                "survived": self.survived_count,
                "unresolved_equivalent": self.unresolved_equivalent_count,
                "invalid": self.invalid_count,
                "errors": self.error_count,
                "runnable": self.runnable_total,
                "statement": self.statement,
                "has_errors": self.has_errors,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def render_text(self) -> str:
        baseline_status = "PASS" if self.baseline_valid else "INVALID"
        lines = [
            "tlaw generated-mutation evidence",
            f"Laws: {self.laws}",
            f"Implementation: {self.implementation}",
            f"Boundary: {self.boundary_kind} ({self.module_name})",
            (
                f"Baseline: {baseline_status}"
                f" ({self.baseline.duration_seconds:.2f}s);"
                f" per-mutant budget {self.budget_seconds:.1f}s"
                f" ({self.timeout_multiplier:g}x baseline,"
                f" floor {self.timeout_floor_seconds:g}s)"
            ),
        ]
        if self.canary is not None:
            if self.canary.wired is True:
                lines.append("Canary: KILLED (laws observe the mutated module)")
            elif self.canary.wired is False:
                lines.append(
                    f"Canary: SURVIVED — INVALID EVIDENCE ({self.canary.note})"
                )
            else:
                lines.append(f"Canary: ERROR — wiring unverified ({self.canary.note})")
        lines.append("Mutations:")
        labels = {
            MutationOutcome.KILLED: "KILLED",
            MutationOutcome.SURVIVED: "SURVIVED",
            MutationOutcome.INVALID: "INVALID",
            MutationOutcome.ERROR: "ERROR",
            MutationOutcome.UNRESOLVED_EQUIVALENT: "UNRESOLVED EQUIVALENT",
        }
        for run in self.runs:
            detail = f" ({run.note})" if run.note else ""
            if run.failed_tests:
                detail = f" (law: {', '.join(run.failed_tests)})"
            lines.append(
                f"- {run.mutation.mutation_id} {labels[run.outcome]}"
                f" {run.mutation.description}{detail}"
            )
        for mutation_id, note in self.stale_ledger_entries:
            lines.append(f"- {mutation_id} STALE LEDGER ENTRY: {note}")
        lines.append(self.statement)
        lines.append(f"Survivors: {self.survived_count}")
        lines.append(
            f"Unresolved equivalent candidates: {self.unresolved_equivalent_count}"
        )
        lines.append(
            f"Invalid or infrastructure errors: {self.invalid_count + self.error_count}"
        )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render_text()


@dataclass(frozen=True)
class _PytestRun:
    exit_code: int
    timed_out: bool
    duration_seconds: float
    output: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Boundary:
    """What one isolated mutation run copies, mutates, and imports.

    Two shapes are supported, and they differ only in what gets copied
    and how the laws reach it:

    - **directory** — laws and implementation share one flat folder. The
      folder is copied whole and the laws run inside the copy, importing
      the module as a sibling (``from implementation import ...``).
    - **package** — the implementation lives in an installed package
      (``src/kvfit/planner.py``). The whole package is copied and placed
      ahead of the installed one on the import path, so intra-package
      imports resolve inside the copy and one coherent world is mutated.
      The laws stay where they are and import the package normally
      (``from kvfit.planner import ...``).

    The package shape is validated in docs/package-boundary-probe.md.
    Whichever shape is used, the canary still proves the laws observe
    the copy before any mutant is scored — a project whose install
    defeats path shadowing reports invalid evidence rather than a page
    of false survivors.
    """

    source: Path
    """Directory copied for every run: the flat folder or package root."""

    module_relative: Path
    """The mutated module's path inside the copied tree."""

    module_name: str
    """Dotted name used for mutation IDs and reports."""

    laws_directory: Path
    """Working directory for pytest."""

    laws_name: str
    """The laws file as pytest should address it."""

    is_package: bool = False

    @property
    def description(self) -> str:
        shape = "package" if self.is_package else "directory"
        return f"{shape} {self.source}"


def _package_root(module: Path) -> Path | None:
    """Return the outermost package directory containing this module.

    Walks up while each directory declares itself a package with an
    ``__init__.py``. Returns None for a module that is not inside a
    package, which is the flat boundary.
    """
    if not (module.parent / "__init__.py").exists():
        return None
    root = module.parent
    while (root.parent / "__init__.py").exists():
        root = root.parent
    return root


def _dotted_import_name(laws: Path, implementation: Path) -> str | None:
    """Read the dotted name the laws use to import this module, statically.

    A namespace package (PEP 420) has no ``__init__.py``, so the
    filesystem alone cannot say whether ``ns/shapes/geometry.py`` is
    imported as ``ns.shapes.geometry``, ``shapes.geometry``, or
    ``geometry`` — it depends on what is on the path. The laws must import
    the module to test it, and that import states the answer. tlaw reads
    it rather than runs it, the same discipline a law-space diff follows.

    Only a dotted name whose tail matches the implementation's own path is
    returned, so a coincidental import of a same-named module elsewhere
    cannot mislead the boundary.
    """
    package_init = implementation.name == "__init__.py"
    stem = implementation.stem
    try:
        tree = ast.parse(laws.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None

    file_parts = (
        implementation.parent.parts
        if package_init
        else implementation.with_suffix("").parts
    )

    def tail_matches(dotted: str) -> str | None:
        parts = dotted.split(".")
        if len(parts) <= 1:
            return None
        if file_parts[-len(parts) :] == tuple(parts):
            return dotted
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if package_init:
                if matched := tail_matches(node.module):
                    return matched
                for alias in node.names:
                    if matched := tail_matches(f"{node.module}.{alias.name}"):
                        return matched
                continue
            # from a.b.c import name  ->  module is a.b.c, tail is c
            if node.module.rsplit(".", 1)[-1] == stem:
                if matched := tail_matches(node.module):
                    return matched
            # from a.b import c  ->  the imported name is the module
            for alias in node.names:
                if alias.name == stem:
                    if matched := tail_matches(f"{node.module}.{stem}"):
                        return matched
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if package_init:
                    if matched := tail_matches(alias.name):
                        return matched
                elif alias.name.rsplit(".", 1)[-1] == stem:
                    if matched := tail_matches(alias.name):
                        return matched
    return None


def describe_boundary(laws: Path, implementation: Path) -> Boundary:
    """Decide which boundary shape applies to one laws/implementation pair.

    Package detection comes first: a module inside a package must be
    mutated as part of that package, even when the laws happen to sit
    beside it, because its siblings import it by dotted name. A regular
    package is found by its ``__init__.py`` chain; a namespace package,
    which has none, is found from how the laws import the module.
    """
    package = _package_root(implementation)
    dotted = _dotted_import_name(laws, implementation)
    if dotted is not None:
        parts = dotted.split(".")
        if implementation.name == "__init__.py":
            top_package = implementation.parents[len(parts) - 1]
            module_relative = Path(*parts) / implementation.name
        else:
            top_package = implementation.parents[len(parts) - 2]
            module_relative = Path(*parts[:-1]) / implementation.name
        # A namespace may contain another namespace or a regular package.
        # In both cases the import names the outer boundary that must be
        # copied; an __init__.py chain alone stops too early in the mixed case.
        if package is None or top_package != package:
            return Boundary(
                source=top_package,
                module_relative=module_relative,
                module_name=dotted,
                laws_directory=laws.parent,
                laws_name=laws.name,
                is_package=True,
            )
    if package is not None:
        relative = implementation.relative_to(package)
        dotted = ".".join((package.name, *relative.with_suffix("").parts))
        # A package's own __init__ names the package itself.
        if implementation.name == "__init__.py":
            dotted = ".".join((package.name, *relative.parts[:-1]))
        return Boundary(
            source=package,
            module_relative=Path(package.name) / relative,
            module_name=dotted,
            laws_directory=laws.parent,
            laws_name=laws.name,
            is_package=True,
        )

    if implementation.parent != laws.parent:
        raise ManifestError(
            "laws and implementation must share one directory unless the "
            "implementation is inside a package: that directory is the "
            "boundary tlaw copies for every isolated run"
        )
    return Boundary(
        source=laws.parent,
        # Relative to the workspace root, which holds the copied directory
        # under its own name, exactly as the package shape does.
        module_relative=Path(laws.parent.name) / implementation.name,
        module_name=implementation.stem,
        laws_directory=laws.parent,
        laws_name=laws.name,
    )


def _run_pytest_in_copy(
    boundary: Boundary,
    *,
    mutated_source: str | None,
    timeout_seconds: float,
) -> _PytestRun:
    """Run the laws once against a fresh copy of the boundary.

    Every run — baseline, canary, mutant, and replay — gets its own copy
    so no bytecode cache, Hypothesis database, or file written by a law
    can leak between runs: the same fresh-start principle tlaw applies to
    paths within a single law. The mutant is written as a real file into
    the copy, so the laws import it the same way they import the true
    implementation.
    """
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        boundary.laws_name,
    )
    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    # Same isolation discipline as `tlaw check`: no third-party pytest
    # plugins, hashing fixed, no bytecode cache pollution.
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(prefix="tlaw-mutation-") as workspace:
        root = Path(workspace) / "boundary"
        root.mkdir()
        destination = root / boundary.source.name
        shutil.copytree(
            boundary.source,
            destination,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".git", ".hypothesis", ".pytest_cache"
            ),
        )
        if mutated_source is not None:
            (root / boundary.module_relative).write_text(
                mutated_source + "\n", encoding="utf-8"
            )

        if boundary.is_package:
            # Run from the neutral copied root. If the laws live beside the
            # original package, using their directory as cwd would put that
            # original ahead of PYTHONPATH and make the canary fail. Importlib
            # mode loads the absolute laws file without prepending its parent;
            # the laws directory remains after the copy so local law helpers
            # are still importable.
            law_file = boundary.laws_directory / boundary.laws_name
            command = (
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--import-mode=importlib",
                str(law_file),
            )
            working_directory = root
            existing = environment.get("PYTHONPATH")
            import_paths = [str(root), str(boundary.laws_directory)]
            if existing:
                import_paths.append(existing)
            environment["PYTHONPATH"] = os.pathsep.join(import_paths)
        else:
            working_directory = destination

        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            duration = time.monotonic() - started
            output = completed.stdout + completed.stderr
            return _PytestRun(
                exit_code=completed.returncode,
                timed_out=False,
                duration_seconds=duration,
                output=output,
                command=command,
            )
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - started
            stdout = (
                error.stdout.decode()
                if isinstance(error.stdout, bytes)
                else error.stdout
            )
            stderr = (
                error.stderr.decode()
                if isinstance(error.stderr, bytes)
                else error.stderr
            )
            output = (stdout or "") + (stderr or "")
            return _PytestRun(
                exit_code=124,
                timed_out=True,
                duration_seconds=duration,
                output=output,
                command=command,
            )


def _unwired_note(boundary: Boundary) -> str:
    """Explain an unkilled canary in terms of the boundary that was used."""
    if boundary.is_package:
        return (
            f"the laws passed while every function in '{boundary.module_name}' "
            "raised; the copied package did not shadow the installed one, so "
            "the laws never observed it — check for an editable install that "
            "uses an import hook rather than a path entry"
        )
    return (
        f"the laws passed while every function in '{boundary.module_name}.py' "
        f"raised; the laws never observed this module — import it as "
        f"'from {boundary.module_name} import ...' so the boundary copy is "
        "what runs"
    )


def _run_one_mutant(
    boundary: Boundary,
    mutation: Mutation,
    *,
    budget_seconds: float,
    baseline_duration: float,
    timeout_multiplier: float,
) -> MutantRun:
    executed = _run_pytest_in_copy(
        boundary,
        mutated_source=mutation.mutated_source,
        timeout_seconds=budget_seconds,
    )
    outcome, note = classify_mutant_exit(
        executed.exit_code, timed_out=executed.timed_out
    )
    if executed.timed_out:
        # Record the calibration so a timeout kill is checkable evidence,
        # not a judgment call: the budget and its derivation are stated.
        note = (
            f"calibrated timeout after {budget_seconds:.1f}s"
            f" ({timeout_multiplier:g}x baseline {baseline_duration:.2f}s)"
        )
    # Name the killing law by the run's one laws file. pytest reports it by
    # the path it was addressed with — in a package run a long climb from a
    # temp workspace, which is ugly and not portable to another machine.
    prefix = f"{boundary.laws_name}::"
    failed = tuple(
        prefix + node.split("::", 1)[1] if "::" in node else node
        for node in _failed_tests(executed.output)
    )
    return MutantRun(
        mutation=mutation,
        outcome=outcome,
        note=note,
        exit_code=executed.exit_code,
        timed_out=executed.timed_out,
        duration_seconds=executed.duration_seconds,
        failed_tests=failed,
        output=executed.output,
    )


def mutate(
    laws: str | Path,
    implementation: str | Path,
    *,
    equivalents: str | Path | None = None,
    timeout_floor_seconds: float = 5.0,
    timeout_multiplier: float = 10.0,
    baseline_timeout_seconds: float = 60.0,
) -> MutationResult:
    """Challenge one laws file with generated mutations of one module.

    A flat implementation must share one directory with its laws; that
    directory is copied whole for every run. An implementation inside a
    package may be targeted from laws anywhere; the whole package is copied
    and shadows the installed package. In both shapes, each run sees exactly
    one coherent world.

    Evidence flow (docs/mutation-evidence.md, with the encoded rules):

    1. run the unmodified boundary; the baseline must pass, and its
       measured duration calibrates every mutant's timeout budget;
    2. generate deterministic single-site mutations of the module;
    3. run the canary — a poisoned implementation in which every function
       raises; if the laws still pass, the laws never observed the
       boundary module and the whole report is INVALID EVIDENCE;
    4. reject mutants that do not compile as invalid without running;
    5. run the unchanged laws against every remaining mutant and classify
       via ``classify_mutant_exit`` (exception = kill, calibrated timeout
       = kill, load failure = invalid, harness trouble = error);
    6. rerun every kill once — a kill that does not replay becomes an
       error rather than inflating strength; and
    7. apply the equivalence ledger to survivors only, keeping stale
       entries visible in the report.
    """
    laws_path = Path(laws).resolve()
    implementation_path = Path(implementation).resolve()
    if laws_path.suffix != ".py" or not laws_path.is_file():
        raise ManifestError(f"laws file does not exist or is not Python: {laws_path}")
    if implementation_path.suffix != ".py" or not implementation_path.is_file():
        raise ManifestError(
            f"implementation does not exist or is not Python: {implementation_path}"
        )
    if implementation_path == laws_path:
        raise ManifestError("laws and implementation must be different files")
    if timeout_floor_seconds <= 0 or timeout_multiplier <= 0:
        raise ValueError("timeout floor and multiplier must be positive")

    ledger = load_equivalence_ledger(equivalents) if equivalents else {}
    boundary = describe_boundary(laws_path, implementation_path)
    implementation_source = implementation_path.read_text(encoding="utf-8")
    try:
        mutations = generate_mutations(implementation_source, boundary.module_name)
    except SyntaxError as error:
        raise ManifestError(
            f"cannot parse implementation {implementation_path}: {error}"
        ) from error

    baseline_run = _run_pytest_in_copy(
        boundary,
        mutated_source=None,
        timeout_seconds=baseline_timeout_seconds,
    )
    baseline = BaselineRun(
        exit_code=baseline_run.exit_code,
        duration_seconds=baseline_run.duration_seconds,
        timed_out=baseline_run.timed_out,
        output=baseline_run.output,
        command=baseline_run.command,
    )

    if not baseline.valid:
        # Rule 1 precondition failed: nothing can be attributed to any
        # mutation, so no mutant may be scored in either direction.
        runs = tuple(
            MutantRun(
                mutation=mutation,
                outcome=MutationOutcome.ERROR,
                note="not run: the baseline run was invalid",
            )
            for mutation in mutations
        )
        runs, stale = apply_equivalence_ledger(runs, ledger)
        return MutationResult(
            laws=str(laws_path),
            implementation=str(implementation_path),
            module_name=boundary.module_name,
            boundary_kind="package" if boundary.is_package else "directory",
            baseline=baseline,
            runs=runs,
            stale_ledger_entries=stale,
            budget_seconds=0.0,
            timeout_multiplier=timeout_multiplier,
            timeout_floor_seconds=timeout_floor_seconds,
        )

    budget_seconds = max(
        timeout_floor_seconds, timeout_multiplier * baseline.duration_seconds
    )

    # The canary runs before any mutant is scored, by default and with no
    # opt-out: it is the guard for the silent worst case, where laws
    # import an installed package instead of the boundary module and
    # every mutant would falsely "survive". A user who has never heard of
    # the canary is exactly who it protects.
    canary: CanaryRun | None = None
    if mutations:
        canary_run = _run_pytest_in_copy(
            boundary,
            mutated_source=poison_source(implementation_source),
            timeout_seconds=budget_seconds,
        )
        canary = CanaryRun(
            exit_code=canary_run.exit_code,
            timed_out=canary_run.timed_out,
            duration_seconds=canary_run.duration_seconds,
            note=(
                _unwired_note(boundary)
                if canary_run.exit_code == 0 and not canary_run.timed_out
                else f"canary run exit code {canary_run.exit_code}"
            ),
            output=canary_run.output,
        )
        if canary.wired is not True:
            # No mutant may be scored in either direction: survivors
            # would be unwired noise and kills would be luck.
            runs = tuple(
                MutantRun(
                    mutation=mutation,
                    outcome=MutationOutcome.ERROR,
                    note="not run: the canary was not killed, so mutant"
                    " outcomes would not be attributable",
                )
                for mutation in mutations
            )
            runs, stale = apply_equivalence_ledger(runs, ledger)
            return MutationResult(
                laws=str(laws_path),
                implementation=str(implementation_path),
                module_name=boundary.module_name,
                boundary_kind="package" if boundary.is_package else "directory",
                baseline=baseline,
                runs=runs,
                stale_ledger_entries=stale,
                budget_seconds=budget_seconds,
                timeout_multiplier=timeout_multiplier,
                timeout_floor_seconds=timeout_floor_seconds,
                canary=canary,
            )

    finished: list[MutantRun] = []
    for mutation in mutations:
        try:
            compile(mutation.mutated_source, f"<mutant {mutation.mutation_id}>", "exec")
        except SyntaxError as error:
            finished.append(
                MutantRun(
                    mutation=mutation,
                    outcome=MutationOutcome.INVALID,
                    note=f"mutant does not compile: {error}",
                )
            )
            continue

        run = _run_one_mutant(
            boundary,
            mutation,
            budget_seconds=budget_seconds,
            baseline_duration=baseline.duration_seconds,
            timeout_multiplier=timeout_multiplier,
        )
        if run.outcome is MutationOutcome.KILLED:
            replay = _run_one_mutant(
                boundary,
                mutation,
                budget_seconds=budget_seconds,
                baseline_duration=baseline.duration_seconds,
                timeout_multiplier=timeout_multiplier,
            )
            if replay.outcome is MutationOutcome.KILLED:
                run = replace(run, note=f"{run.note}; replay confirmed")
            else:
                # Rule 3: a kill that does not replay is an error. This
                # is deliberately the less flattering direction — tlaw
                # claims less strength instead of counting luck.
                run = replace(
                    run,
                    outcome=MutationOutcome.ERROR,
                    note=(
                        "kill did not replay: first run killed,"
                        f" replay {replay.outcome.value} ({replay.note})"
                    ),
                )
        finished.append(run)

    runs, stale = apply_equivalence_ledger(tuple(finished), ledger)
    return MutationResult(
        laws=str(laws_path),
        implementation=str(implementation_path),
        module_name=boundary.module_name,
        boundary_kind="package" if boundary.is_package else "directory",
        baseline=baseline,
        runs=runs,
        stale_ledger_entries=stale,
        budget_seconds=budget_seconds,
        timeout_multiplier=timeout_multiplier,
        timeout_floor_seconds=timeout_floor_seconds,
        canary=canary,
    )
