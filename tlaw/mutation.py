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

How: the evidence rules this module encodes
------------------------------------------
1. **Attribution by difference from baseline.** The baseline run must pass
   with complete law obligations before any mutant runs. Ordinary runs must
   retain the same law inventory, including parametrized items. A retained
   law failure under a mutant is a candidate kill. ``error`` covers outcomes
   without complete execution evidence, a replayable law failure, or another
   attributable outcome. A mutant that breaks collection could not be loaded at
   all, so it is ``invalid``: never a kill, never a law success.

2. **A calibrated timeout is a kill, and is counted apart.** An infinite
   loop is behavior, not infrastructure. The per-mutant budget is derived
   from the measured baseline duration (``max(floor, multiplier *
   baseline)``), and that calibration is recorded in the report. Only a
   timeout without a measured baseline would be an error; this engine
   always measures. But a timeout is a kill *no law reported*: it carries
   no attribution and can never appear in the which-law-defends-this
   reading of a report, so the summary states its count separately rather
   than letting it borrow weight from kills that name a law.

3. **A kill that does not replay is an error, not a kill — and replaying
   means the same evidence, not the same label.** Every kill is rerun
   once in a fresh workspace and must come back as the same *kind*
   (``KillKind``): a timeout cannot confirm a law failure, or the reverse,
   because the two runs would be disagreeing about what happened. An
   observed kill must additionally name the same laws, since attribution
   is read out of exactly that field. A timeout kill re-measures the
   baseline immediately before its replay and recomputes the budget from
   that fresh measurement, so a machine that became contended after the
   run began cannot manufacture a kill out of two slow runs. Laws use
   deterministic generation by default. Observed kills replay their retained
   typed inputs and exception fingerprints against the same frozen mutant;
   timeout kills must exceed the freshly calibrated budget. Unconfirmed
   differences are errors rather than evidence of strength.
   This is the only place tlaw ever downgrades a failure, and it
   downgrades toward claiming less.

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
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal

from .audit import TARGET_ENV, ManifestError
from .finite import DEFAULT_MAX_CASES, MAX_CASES_ENV
from .obligations import (
    OBLIGATIONS_ENV,
    Obligation,
    evidence_line,
    obligations_complete,
    read_obligations,
)
from .replay import (
    CAPTURE_ENV,
    MODE_ENV,
    REPLAY_ENV,
    REPLAY_ROOT_ENV,
    SEED_ENV,
    build_check_report,
    load_report,
    read_capture,
)


class MutationOutcome(Enum):
    """The honest outcomes from docs/mutation-evidence.md.

    Invalid and error are never kills; an unresolved equivalent is a
    survivor with a committed explanation, still visible in every report.
    """

    KILLED = "killed"
    SURVIVED = "survived"
    INVALID = "invalid"
    ERROR = "error"
    NOT_RUN = "not-run"
    EXCLUDED = "excluded"
    UNRESOLVED_EQUIVALENT = "unresolved-equivalent"


class KillKind(Enum):
    """How a kill was observed, which decides what may confirm it.

    A kill is two facts, not one: that the mutant behaved differently from
    the baseline, and *how* that difference showed up. The two kinds are
    not interchangeable evidence:

    - ``OBSERVED`` — pytest reported failures, so named laws rejected the
      mutant and the run carries attribution in ``failed_tests``; and
    - ``TIMEOUT`` — the run exceeded its calibrated budget. That is a
      behavioral difference (rule 2), but no law reported anything, so the
      run carries no attribution and can never appear in the
      which-law-defends-this reading of a report.

    Because they are different claims, a replay may confirm a kill only
    when it reproduces the *same* kind. A timeout confirmed by a law
    failure, or a law failure confirmed by a timeout, is two runs
    disagreeing about what happened, which is not confirmation.
    """

    OBSERVED = "observed"
    TIMEOUT = "timeout"


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
    obligations: tuple[Obligation, ...] = ()
    evidence: dict[str, Any] = field(repr=False, default_factory=dict)
    evidence_error: str | None = None

    @property
    def wired(self) -> bool | None:
        if self.timed_out:
            # The baseline finished and the canary did not: the laws
            # observed a behavioral difference, which is all the canary
            # needs to prove.
            return True
        if self.evidence_error or not obligations_complete(self.obligations):
            return None
        if self.exit_code == 1:
            return True
        if self.exit_code == 0:
            return False
        return None


def classify_mutant_exit(
    exit_code: int, *, timed_out: bool
) -> tuple[MutationOutcome, KillKind | None, str]:
    """Map a process exit to a provisional outcome, kill kind, and note.

    The runner also requires complete law evidence and confirms candidate
    kills before counting them.

    This function is only called when the baseline already passed cleanly
    on the same laws with the same deterministic examples, so:

    - a timeout is a kill, because the budget was calibrated against the
      measured baseline and an infinite loop is behavior (rule 2). Its
      kind is ``TIMEOUT``: real evidence, but evidence no law reported;
    - pytest exit 1 is a kill, whether the law compared ``False`` or the
      mutated implementation raised — both are behavioral differences the
      baseline did not show (rule 1). Its kind is ``OBSERVED``;
    - pytest exit 2 means collection failed, and since the baseline
      collected these same laws, the mutant could not be loaded: invalid,
      never a kill and never a law success; and
    - every other exit is a harness problem the mutation cannot be
      blamed or credited for: error.

    The kind is ``None`` for every non-kill, because only a kill has one.
    """
    if timed_out:
        return MutationOutcome.KILLED, KillKind.TIMEOUT, "calibrated timeout"
    if exit_code == 0:
        return MutationOutcome.SURVIVED, None, "all laws passed"
    if exit_code == 1:
        return (
            MutationOutcome.KILLED,
            KillKind.OBSERVED,
            "law failure or implementation exception",
        )
    if exit_code == 2:
        return MutationOutcome.INVALID, None, "mutant could not be collected or loaded"
    return MutationOutcome.ERROR, None, f"harness exit code {exit_code}"


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
    obligations: tuple[Obligation, ...] = ()
    evidence_error: str | None = None

    @property
    def valid(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and self.evidence_error is None
            and obligations_complete(self.obligations)
            and all(law.status == "passed" for law in self.obligations)
        )


@dataclass(frozen=True)
class MutantRun:
    """One mutant's final outcome plus the evidence behind it.

    ``exit_code`` is None when the mutant never ran (it was excluded, failed
    to compile, a prerequisite failed, planning was requested, or the budget
    stopped it).
    ``failed_tests`` names the exact laws
    that rejected the mutant, so a law change can later say which
    survivors it kills.

    ``kill_kind`` is set only on a kill and says which evidence produced
    it. A ``TIMEOUT`` kill has no ``failed_tests``, because no law
    reported anything: reading such a run as attribution would credit a
    law that never spoke.
    """

    mutation: Mutation
    outcome: MutationOutcome
    note: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    failed_tests: tuple[str, ...] = ()
    output: str = field(repr=False, default="")
    kill_kind: KillKind | None = None
    evidence: dict[str, Any] = field(repr=False, default_factory=dict)
    replay_evidence: dict[str, Any] = field(repr=False, default_factory=dict)
    replay_method: Literal["exact", "timeout"] | None = None
    replay_exit_code: int | None = None
    replay_timed_out: bool | None = None
    replay_duration_seconds: float | None = None


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
    every report and in the runnable denominator. Unrun and excluded entries
    remain unverified: neither can support or contradict a declaration.
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
        elif outcome not in (
            MutationOutcome.SURVIVED,
            MutationOutcome.NOT_RUN,
            MutationOutcome.EXCLUDED,
        ):
            stale.append(
                (mutation_id, f"contradicted by evidence: mutant was {outcome.value}")
            )
    return tuple(updated), tuple(stale)


@dataclass(frozen=True)
class CampaignPlan:
    """Selected-campaign estimates, conditional on passing baseline and canary.

    Runs count isolated pytest invocations, not seconds or memory. Finite
    case attempts assume the baseline law inventory and domains stay intact.
    """

    generated_mutations: int
    runnable_mutations: int
    law_cases: int
    cases_per_run: int | None
    min_runs: int
    max_runs: int
    max_case_attempts: int | None
    excluded_mutations: int = 0

    @property
    def selected_mutations(self) -> int:
        return self.generated_mutations - self.excluded_mutations


class _RunLimitReached(Exception):
    """A scheduling decision, never a calibrated subprocess timeout."""


@dataclass
class _RunBudget:
    """One owner for all isolated invocations in a campaign."""

    max_runs: int | None
    runs_started: int = 0
    stopped_before: str | None = None

    def require_available(self, purpose: str) -> None:
        if self.max_runs is not None and self.runs_started >= self.max_runs:
            if self.stopped_before is None:
                self.stopped_before = purpose
            raise _RunLimitReached(f"campaign run budget exhausted before {purpose}")


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
    # None when the canary never ran: no mutations to score, an invalid
    # baseline, plan-only mode, or an exhausted run budget. Every scored
    # mutant requires the canary to establish wiring first.
    canary: CanaryRun | None = None
    max_cases: int | None = None
    plan: CampaignPlan | None = None
    plan_only: bool = False
    max_runs: int | None = None
    runs_started: int = 0
    stopped_before: str | None = None

    @property
    def method(self) -> Literal["sampled", "exhaustive"]:
        return "sampled" if self.max_cases is None else "exhaustive"

    def _count(self, outcome: MutationOutcome) -> int:
        return sum(run.outcome is outcome for run in self.runs)

    @property
    def baseline_valid(self) -> bool:
        return self.baseline.valid

    @property
    def killed_count(self) -> int:
        return self._count(MutationOutcome.KILLED)

    @property
    def killed_by_timeout_count(self) -> int:
        """Kills that no law reported, and so carry no attribution.

        A timeout kill is real evidence of behavioral difference (rule 2)
        and belongs in the numerator. It can never appear in the
        which-law-defends-this reading of a report, because no law
        reported anything. Counting it separately keeps the numerator
        decomposable, so the strength statement cannot quietly borrow
        weight from kills that name nothing.
        """
        return sum(
            run.outcome is MutationOutcome.KILLED and run.kill_kind is KillKind.TIMEOUT
            for run in self.runs
        )

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
    def not_run_count(self) -> int:
        return self._count(MutationOutcome.NOT_RUN)

    @property
    def excluded_count(self) -> int:
        return self._count(MutationOutcome.EXCLUDED)

    @property
    def selected_count(self) -> int:
        return len(self.runs) - self.excluded_count

    @property
    def selection_complete(self) -> bool:
        return not self.plan_only and not self.has_errors

    @property
    def complete(self) -> bool:
        return self.selection_complete and self.excluded_count == 0

    @property
    def runnable_total(self) -> int:
        # Invalid, error, excluded and unrun candidates produced no evidence about the
        # laws, in either direction, so they stay outside the denominator.
        return (
            self.killed_count + self.survived_count + self.unresolved_equivalent_count
        )

    @property
    def has_errors(self) -> bool:
        canary_failed = self.canary is not None and self.canary.wired is not True
        no_challenges = self.selected_count == 0
        return (
            (not self.baseline_valid)
            or canary_failed
            or no_challenges
            or self.error_count > 0
            or (self.not_run_count > 0 and not self.plan_only)
            or self.stopped_before is not None
        )

    @property
    def statement(self) -> str:
        if not self.runs:
            return "No supported mutations were generated; no mutation evidence exists."
        if self.plan_only:
            return "Campaign plan only; no mutation evidence was established."
        scope = "selected" if self.excluded_count else "generated"
        statement = (
            f"Killed {self.killed_count} of {self.runnable_total} "
            f"runnable {scope} mutations."
        )
        if self.not_run_count or self.stopped_before is not None:
            statement += (
                f" Campaign incomplete; {self.not_run_count} mutations not run."
            )
        if self.excluded_count:
            statement += (
                f" {self.excluded_count} mutations excluded; "
                "evidence covers the selected scope only."
            )
        return statement

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "tlaw.mutation/v1",
            "method": self.method,
            "max_cases": self.max_cases,
            "plan_only": self.plan_only,
            "plan": (
                None
                if self.plan is None
                else asdict(self.plan)
                | {"selected_mutations": self.plan.selected_mutations}
            ),
            "work": {
                "max_runs": self.max_runs,
                "runs_started": self.runs_started,
                "runs_remaining": (
                    None if self.max_runs is None else self.max_runs - self.runs_started
                ),
                "stopped_before": self.stopped_before,
            },
            "laws": self.laws,
            "implementation": self.implementation,
            "boundary": {"kind": self.boundary_kind, "module": self.module_name},
            "baseline": {
                "exit_code": self.baseline.exit_code,
                "duration_seconds": self.baseline.duration_seconds,
                "timed_out": self.baseline.timed_out,
                "valid": self.baseline_valid,
                "obligations": [law.to_dict() for law in self.baseline.obligations],
                "evidence_error": self.baseline.evidence_error,
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
                    "obligations": [law.to_dict() for law in self.canary.obligations],
                    "evidence": self.canary.evidence,
                    "evidence_error": self.canary.evidence_error,
                }
            ),
            "mutations": [
                {
                    "id": run.mutation.mutation_id,
                    "operator": run.mutation.operator,
                    "qualname": run.mutation.qualname,
                    "description": run.mutation.description,
                    "outcome": run.outcome.value,
                    "selected": run.outcome is not MutationOutcome.EXCLUDED,
                    "kill_kind": None if run.kill_kind is None else run.kill_kind.value,
                    "note": run.note,
                    "exit_code": run.exit_code,
                    "timed_out": run.timed_out,
                    "duration_seconds": run.duration_seconds,
                    "failed_tests": list(run.failed_tests),
                    "diff": run.mutation.diff,
                    "evidence": run.evidence,
                    "replay_evidence": run.replay_evidence,
                    "replay_method": run.replay_method,
                    "replay_exit_code": run.replay_exit_code,
                    "replay_timed_out": run.replay_timed_out,
                    "replay_duration_seconds": run.replay_duration_seconds,
                }
                for run in self.runs
            ],
            "stale_equivalence_entries": [
                {"id": mutation_id, "note": note}
                for mutation_id, note in self.stale_ledger_entries
            ],
            "summary": {
                "killed": self.killed_count,
                "killed_by_timeout": self.killed_by_timeout_count,
                "survived": self.survived_count,
                "unresolved_equivalent": self.unresolved_equivalent_count,
                "invalid": self.invalid_count,
                "errors": self.error_count,
                "not_run": self.not_run_count,
                "selected": self.selected_count,
                "excluded": self.excluded_count,
                "selection_complete": self.selection_complete,
                "complete": self.complete,
                "runnable": self.runnable_total,
                "generated": len(self.runs),
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
                "Checking: sampled; total input space not enumerated"
                if self.max_cases is None
                else f"Checking: exhaustive; at most {self.max_cases} cases per law case"
            ),
            (
                f"Baseline: {baseline_status}"
                f" ({self.baseline.duration_seconds:.2f}s);"
                f" per-mutant budget {self.budget_seconds:.1f}s"
                f" ({self.timeout_multiplier:g}x baseline,"
                f" floor {self.timeout_floor_seconds:g}s)"
            ),
        ]
        completed = sum(law.completed for law in self.baseline.obligations)
        lines.append(
            f"Baseline law obligations: {completed} of "
            f"{len(self.baseline.obligations)} completed"
        )
        lines.extend(f"  {evidence_line(law)}" for law in self.baseline.obligations)
        lines.append(
            f"Mutation scope: generated {len(self.runs)}; selected {self.selected_count}; "
            f"excluded {self.excluded_count}"
        )
        scope = "selected campaign" if self.excluded_count else "full campaign"
        if self.plan is not None:
            plan = self.plan
            lines.append(
                f"Campaign plan: generated mutations {plan.generated_mutations}; "
                f"selected {plan.selected_mutations}; excluded {plan.excluded_mutations}; "
                f"compiling mutations {plan.runnable_mutations}; law cases {plan.law_cases}"
            )
            lines.append(
                f"Planned isolated runs: {plan.min_runs}..{plan.max_runs} "
                f"for the {scope}, including baseline, canary and confirmations"
            )
            if plan.cases_per_run is None:
                lines.append("Planned law input counts: unknown (sampled checks)")
            else:
                lines.append(
                    f"Planned finite law cases per ordinary run: {plan.cases_per_run}; "
                    f"{scope} case attempts at most {plan.max_case_attempts} "
                    "if the baseline inventory and domains remain unchanged"
                )
        else:
            lines.append("Campaign plan unavailable")
        limit = "unlimited" if self.max_runs is None else str(self.max_runs)
        lines.append(f"Isolated runs started: {self.runs_started}; run budget: {limit}")
        if self.plan_only:
            lines.append("Plan only: baseline executed; canary and mutants not run")
        if self.stopped_before is not None:
            lines.append(f"Run budget exhausted before {self.stopped_before}")
        if self.baseline.evidence_error:
            lines.append(f"Baseline evidence: {self.baseline.evidence_error}")
        if self.canary is not None:
            if self.canary.wired is True:
                detail = (
                    self.canary.note
                    if self.canary.timed_out
                    else "laws observe the mutated module"
                )
                lines.append(f"Canary: KILLED ({detail})")
            elif self.canary.wired is False:
                lines.append(
                    f"Canary: SURVIVED — INVALID EVIDENCE ({self.canary.note})"
                )
            else:
                lines.append(f"Canary: ERROR — wiring unverified ({self.canary.note})")
            lines.extend(f"  {evidence_line(law)}" for law in self.canary.obligations)
        lines.append("Mutations:")
        labels = {
            MutationOutcome.KILLED: "KILLED",
            MutationOutcome.SURVIVED: "SURVIVED",
            MutationOutcome.INVALID: "INVALID",
            MutationOutcome.ERROR: "ERROR",
            MutationOutcome.NOT_RUN: "NOT RUN",
            MutationOutcome.EXCLUDED: "EXCLUDED",
            MutationOutcome.UNRESOLVED_EQUIVALENT: "UNRESOLVED EQUIVALENT",
        }
        if not self.runs:
            lines.append("- NONE — INVALID EVIDENCE (no supported mutation sites)")
        for run in self.runs:
            details = [run.note] if run.note else []
            if (
                run.outcome is MutationOutcome.KILLED
                and run.kill_kind is KillKind.OBSERVED
                and run.failed_tests
            ):
                details.append(f"law: {', '.join(run.failed_tests)}")
            detail = f" ({'; '.join(details)})" if details else ""
            lines.append(
                f"- {run.mutation.mutation_id} {labels[run.outcome]}"
                f" {run.mutation.description}{detail}"
            )
            for entry in run.evidence.get("obligations", ()):
                lines.append(f"  {evidence_line(Obligation(**entry))}")
            if run.timed_out and not run.evidence.get("obligations"):
                lines.append("  case counts unavailable: execution timed out")
        for mutation_id, note in self.stale_ledger_entries:
            lines.append(f"- {mutation_id} STALE LEDGER ENTRY: {note}")
        lines.append(self.statement)
        if self.killed_by_timeout_count:
            lines.append(
                "Kills by calibrated timeout (no law attribution):"
                f" {self.killed_by_timeout_count}"
            )
        lines.append(f"Survivors: {self.survived_count}")
        lines.append(f"Mutations not run: {self.not_run_count}")
        lines.append(f"Mutations excluded from selection: {self.excluded_count}")
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
    obligations: tuple[Obligation, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_error: str | None = None


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
    replay_evidence: dict[str, Any] | None = None,
    max_cases: int | None = None,
    work: _RunBudget | None = None,
    purpose: str = "law run",
) -> _PytestRun:
    """Run the laws once against a fresh copy of the boundary.

    Every run — baseline, canary, mutant, and replay — gets its own copy
    so no bytecode cache, Hypothesis database, or file written by a law
    can leak between runs: the same fresh-start principle tlaw applies to
    paths within a single law. The mutant is written as a real file into
    the copy, so the laws import it the same way they import the true
    implementation.
    """
    if work is not None:
        work.require_available(purpose)
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "tlaw.pytest_plugin",
        # Same reason as ``tlaw check``: third-party plugin autoload is off,
        # so an inherited ``addopts`` naming an absent plugin would make every
        # mutant fail to start and be scored as a meaningless kill.
        "-o",
        "addopts=",
        boundary.laws_name,
    )
    environment = os.environ.copy()
    environment.pop(TARGET_ENV, None)
    environment.pop("PYTEST_ADDOPTS", None)
    for variable in (
        CAPTURE_ENV,
        OBLIGATIONS_ENV,
        MODE_ENV,
        SEED_ENV,
        REPLAY_ENV,
        REPLAY_ROOT_ENV,
        MAX_CASES_ENV,
        "TLAW_EXPLAIN",
    ):
        environment.pop(variable, None)
    # Same isolation discipline as `tlaw check`: no third-party pytest
    # plugins, hashing fixed, no bytecode cache pollution.
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    mode = "default"
    if replay_evidence is not None:
        mode = "replay"
    elif max_cases is not None:
        mode = "exhaustive"

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
                "-p",
                "tlaw.pytest_plugin",
                "-o",
                "addopts=",
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
            law_file = destination / boundary.laws_name

        law_file = law_file.resolve()
        receipt = Path(workspace) / "obligations.json"
        capture = Path(workspace) / "failures.json"
        environment[OBLIGATIONS_ENV] = str(receipt)
        if replay_evidence is None:
            environment[CAPTURE_ENV] = str(capture)
            environment[MODE_ENV] = mode
            if max_cases is not None:
                environment[MAX_CASES_ENV] = str(max_cases)
        else:
            report_path = Path(workspace) / "replay.json"
            report_path.write_text(json.dumps(replay_evidence), encoding="utf-8")
            try:
                load_report(report_path)
            except ManifestError as error:
                return _PytestRun(
                    4, False, 0.0, str(error), command, evidence_error=str(error)
                )
            environment[REPLAY_ENV] = str(report_path)
            environment[REPLAY_ROOT_ENV] = str(law_file.parent)

        if work is not None:
            work.runs_started += 1
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
            output = completed.stdout + completed.stderr
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as error:
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
            exit_code = 124
            timed_out = True
        duration = time.monotonic() - started
        obligations: tuple[Obligation, ...] = ()
        evidence: dict[str, Any] = {}
        evidence_error = None
        try:
            collected = read_obligations(receipt)
            evidence = build_check_report(
                law_file,
                working_directory=law_file.parent,
                mode=mode,
                seed=None,
                pytest_exit_code=exit_code,
                captured=read_capture(capture),
                obligations=collected,
            )
            # Copies have different absolute roots; law identities and item
            # suffixes, including parametrization, remain the same evidence.
            if any("::" not in entry["nodeid"] for entry in evidence["obligations"]):
                raise ManifestError(
                    "law execution evidence has an invalid pytest node ID"
                )
            obligations = tuple(
                Obligation(
                    **{
                        **entry,
                        "nodeid": (
                            f"{boundary.laws_name}::{entry['nodeid'].split('::', 1)[1]}"
                        ),
                    }
                )
                for entry in evidence["obligations"]
            )
            if len({law.nodeid for law in obligations}) != len(obligations):
                raise ManifestError(
                    "law execution evidence has duplicate pytest node IDs"
                )
            evidence["obligations"] = [law.to_dict() for law in obligations]
        except (ManifestError, KeyError, TypeError, ValueError, OSError) as error:
            evidence_error = str(error)
            output += f"\ntlaw mutation: {evidence_error}\n"
        return _PytestRun(
            exit_code,
            timed_out,
            duration,
            output,
            command,
            obligations,
            evidence,
            evidence_error,
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


def _inventory(obligations: tuple[Obligation, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((law.identity, law.nodeid) for law in obligations))


def _evidence_problem(
    run: _PytestRun,
    *,
    expected: tuple[Obligation, ...] | None = None,
    require_retained: bool = True,
    max_cases: int | None = None,
) -> str | None:
    """Admit ordinary outcomes only after their law execution is accounted for."""
    if run.evidence_error:
        return run.evidence_error
    if not obligations_complete(run.obligations):
        incomplete = ", ".join(
            f"{law.nodeid}: {law.status}"
            for law in run.obligations
            if not law.completed
        )
        return f"incomplete law obligations: {incomplete or 'no laws collected'}"
    if expected is not None and _inventory(run.obligations) != _inventory(expected):
        return "law inventory differs from the baseline"
    method = "sampled" if max_cases is None else "exhaustive"
    for law in run.obligations:
        if law.method != method:
            return f"law checking method differs from requested {method} mode"
        if max_cases is not None and (
            law.cases_total is None or not 0 < law.cases_total <= max_cases
        ):
            return "law case count exceeds the requested exhaustive limit"
    if expected is not None:
        baseline = {law.nodeid: law for law in expected}
        for law in run.obligations:
            original = baseline[law.nodeid]
            if (
                law.method != original.method
                or law.cases_total != original.cases_total
                or law.list_bounds != original.list_bounds
            ):
                return "law checking method, case counts or bounds differ from the baseline"
    failures = run.evidence.get("failures", ())
    failed = {law.identity for law in run.obligations if law.status == "failed"}
    if run.exit_code == 0 and (failed or failures):
        return "passing pytest run contains failed law evidence"
    if run.exit_code == 1:
        if not failed or {failure["identity"] for failure in failures} != failed:
            return "pytest failed without matching retained law failures"
        if require_retained and any(
            failure.get("capture_error") for failure in failures
        ):
            return (
                "law was refuted, but its counterexample cannot be retained for replay"
            )
    return None


def _replay_problem(run: _PytestRun, evidence: dict[str, Any]) -> str | None:
    if run.timed_out:
        return "exact replay timed out"
    if run.evidence_error:
        return run.evidence_error
    if run.exit_code != 0 or not obligations_complete(run.obligations):
        return "retained failures did not reproduce with complete law execution"
    expected: dict[str, int] = {}
    for failure in evidence["failures"]:
        identity = failure["identity"]
        expected[identity] = expected.get(identity, 0) + 1
    actual = {law.identity for law in run.obligations}
    if actual != set(expected):
        return (
            f"attribution did not replay: retained failures name {', '.join(sorted(expected))};"
            f" replay names {', '.join(sorted(actual)) or 'no law'}"
        )
    if len(run.obligations) != len(expected):
        return "replay law inventory differs from retained failures"
    for law in run.obligations:
        count = expected.get(law.identity)
        if (
            law.status != "passed"
            or law.method != "replay"
            or law.cases_checked != count
            or law.cases_total != count
        ):
            return "replay did not execute every retained input exactly once"
    return None


def _run_one_mutant(
    boundary: Boundary,
    mutation: Mutation,
    *,
    budget_seconds: float,
    baseline_duration: float,
    timeout_multiplier: float,
    expected: tuple[Obligation, ...],
    max_cases: int | None = None,
    work: _RunBudget | None = None,
) -> MutantRun:
    executed = _run_pytest_in_copy(
        boundary,
        mutated_source=mutation.mutated_source,
        timeout_seconds=budget_seconds,
        max_cases=max_cases,
        work=work,
        purpose=f"mutant {mutation.mutation_id}",
    )
    outcome, kill_kind, note = classify_mutant_exit(
        executed.exit_code, timed_out=executed.timed_out
    )
    if executed.timed_out:
        # Record the calibration so a timeout kill is checkable evidence,
        # not a judgment call: the budget and its derivation are stated.
        note = (
            f"calibrated timeout after {budget_seconds:.1f}s"
            f" ({timeout_multiplier:g}x baseline {baseline_duration:.2f}s)"
        )
    elif executed.exit_code in (0, 1):
        problem = _evidence_problem(executed, expected=expected, max_cases=max_cases)
        if problem:
            outcome, kill_kind, note = MutationOutcome.ERROR, None, problem
    # A timeout's partial receipt is history, not law attribution. Errors
    # also retain their diagnostic evidence without claiming a killing law.
    failed = (
        tuple(law.nodeid for law in executed.obligations if law.status == "failed")
        if outcome is MutationOutcome.KILLED and kill_kind is KillKind.OBSERVED
        else ()
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
        kill_kind=kill_kind,
        evidence=executed.evidence,
    )


def _kill_did_not_replay(run: MutantRun, note: str) -> MutantRun:
    """Downgrade a kill to an error, keeping the reason visible.

    Rule 3 has one direction: claim less strength rather than count a
    difference that did not reproduce. An error is not a kill, so the
    downgraded run keeps neither a kill kind nor ``failed_tests`` — the
    first run's attribution moves into the note, where it reads as
    history instead of as a law that rejected this mutant. That keeps one
    invariant true everywhere a report is consumed: a run naming laws is
    a confirmed observed kill.
    """
    if run.failed_tests:
        note += f"; first run named {', '.join(run.failed_tests)}"
    return replace(
        run,
        outcome=MutationOutcome.ERROR,
        note=note,
        kill_kind=None,
        failed_tests=(),
    )


def _confirm_kill(
    boundary: Boundary,
    mutation: Mutation,
    run: MutantRun,
    *,
    budget_seconds: float,
    baseline_duration: float,
    timeout_multiplier: float,
    timeout_floor_seconds: float,
    baseline_timeout_seconds: float,
    expected: tuple[Obligation, ...],
    max_cases: int | None = None,
    work: _RunBudget | None = None,
) -> MutantRun:
    """Confirm retained failures exactly, or repeat a freshly timed timeout.

    Observed failures replay the original typed inputs and exception
    fingerprints without generating new examples. A timeout has no retained
    law failure: re-measure the complete baseline immediately before retrying
    it, so contention cannot turn two slow runs into a confirmed kill.
    """
    if run.kill_kind is KillKind.TIMEOUT:
        try:
            fresh = _run_pytest_in_copy(
                boundary,
                mutated_source=None,
                timeout_seconds=baseline_timeout_seconds,
                max_cases=max_cases,
                work=work,
                purpose=f"timeout baseline for {mutation.mutation_id}",
            )
        except _RunLimitReached as error:
            return _kill_did_not_replay(run, f"confirmation not run: {error}")
        problem = _evidence_problem(fresh, expected=expected, max_cases=max_cases)
        if (
            fresh.exit_code != 0
            or fresh.timed_out
            or problem
            or any(law.status != "passed" for law in fresh.obligations)
        ):
            return _kill_did_not_replay(
                run,
                "timeout kill not confirmed: the re-measured baseline no longer"
                f" passes (exit code {fresh.exit_code},"
                f" timed out {str(fresh.timed_out).lower()};"
                f" {problem or 'baseline did not pass'}), so there is no"
                " calibration left to time this mutant against",
            )
        replay_baseline = fresh.duration_seconds
        replay_budget = max(timeout_floor_seconds, timeout_multiplier * replay_baseline)
        replay_evidence = None
        method = "timeout"
    else:
        replay_baseline = baseline_duration
        replay_budget = budget_seconds
        replay_evidence = run.evidence
        method = "exact"

    purpose = "timeout retry" if method == "timeout" else "exact replay"
    try:
        replay = _run_pytest_in_copy(
            boundary,
            mutated_source=mutation.mutated_source,
            timeout_seconds=replay_budget,
            replay_evidence=replay_evidence,
            max_cases=max_cases if replay_evidence is None else None,
            work=work,
            purpose=f"{purpose} for {mutation.mutation_id}",
        )
    except _RunLimitReached as error:
        return _kill_did_not_replay(run, f"confirmation not run: {error}")
    run = replace(
        run,
        replay_method=method,
        replay_evidence=replay.evidence,
        replay_exit_code=replay.exit_code,
        replay_timed_out=replay.timed_out,
        replay_duration_seconds=replay.duration_seconds,
    )
    if run.kill_kind is KillKind.TIMEOUT:
        problem = None if replay.timed_out else "calibrated timeout did not repeat"
    else:
        problem = _replay_problem(replay, run.evidence)
    if problem:
        return _kill_did_not_replay(
            replace(run, output=run.output + "\n--- replay ---\n" + replay.output),
            f"kill did not replay: {problem}",
        )
    if run.kill_kind is KillKind.TIMEOUT:
        return replace(
            run,
            note=(
                f"{run.note}; replay confirmed against a baseline re-measured"
                f" at {replay_baseline:.2f}s (budget {replay_budget:.1f}s)"
            ),
        )
    return replace(run, note=f"{run.note}; exact replay confirmed")


def mutate(
    laws: str | Path,
    implementation: str | Path,
    *,
    equivalents: str | Path | None = None,
    timeout_floor_seconds: float = 5.0,
    timeout_multiplier: float = 10.0,
    baseline_timeout_seconds: float = 60.0,
    exhaustive: bool = False,
    max_cases: int | None = None,
    plan_only: bool = False,
    max_runs: int | None = None,
    mutant_ids: Sequence[str] | None = None,
) -> MutationResult:
    """Challenge one laws file with generated mutations of one module.

    A flat implementation must share one directory with its laws; that
    directory is copied whole for every run. An implementation inside a
    package may be targeted from laws anywhere; the whole package is copied
    and shadows the installed package. In both shapes, each run sees exactly
    one coherent world.

    ``exhaustive=True`` enumerates the declared finite inputs for every
    ordinary run, with ``max_cases`` combinations per law case (default 1,000).
    Exact replay executes only retained inputs. A survivor exhausts the declared
    space; a failing law records how many cases it checked before stopping.

    ``plan_only=True`` executes one baseline to establish a campaign plan,
    then leaves mutants unrun. ``max_runs`` caps all isolated pytest invocations,
    including baseline, canary and confirmations. It does not alter calibrated
    deadlines. An unconfirmed candidate is never credited as a kill.

    ``mutant_ids`` selects exact IDs from the current generated inventory.
    Unknown IDs and empty selections are rejected before execution. Repeated
    IDs run once, in generation order. Excluded mutants remain visible without
    evidence; reruns establish fresh evidence rather than reuse prior results.

    Evidence flow (docs/mutation-evidence.md, with the encoded rules):

    1. generate deterministic single-site mutations and validate any exact IDs;
    2. run the unmodified boundary; the baseline must pass, and its
       measured duration calibrates every mutant's timeout budget;
    3. run the canary — a poisoned implementation in which every function
       raises; if the laws still pass, the laws never observed the
       boundary module and the whole report is INVALID EVIDENCE;
    4. reject mutants that do not compile as invalid without running;
    5. require matching complete law obligations and retained failures for
       ordinary candidate kills; preserve calibrated timeout attribution;
    6. replay retained failures exactly, or repeat a freshly calibrated timeout;
       an unconfirmed candidate becomes an error; and
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

    if max_cases is not None:
        if not exhaustive:
            raise ValueError("max_cases requires exhaustive=True")
        if type(max_cases) is not int or max_cases <= 0:
            raise ValueError("max_cases must be a positive integer")
    if exhaustive and max_cases is None:
        max_cases = DEFAULT_MAX_CASES

    if max_runs is not None and (type(max_runs) is not int or max_runs <= 0):
        raise ValueError("max_runs must be a positive integer")

    ledger = load_equivalence_ledger(equivalents) if equivalents else {}
    boundary = describe_boundary(laws_path, implementation_path)
    implementation_source = implementation_path.read_text(encoding="utf-8")
    try:
        mutations = generate_mutations(implementation_source, boundary.module_name)
    except SyntaxError as error:
        raise ManifestError(
            f"cannot parse implementation {implementation_path}: {error}"
        ) from error

    selected_ids = {mutation.mutation_id for mutation in mutations}
    if mutant_ids is not None:
        if (
            isinstance(mutant_ids, (str, bytes))
            or not isinstance(mutant_ids, Sequence)
            or not mutant_ids
            or any(
                not isinstance(identifier, str) or not identifier
                for identifier in mutant_ids
            )
        ):
            raise ManifestError("mutant_ids must be a non-empty sequence of exact IDs")
        requested = set(mutant_ids)
        unknown = requested - selected_ids
        if unknown:
            raise ManifestError(f"unknown mutant IDs: {', '.join(sorted(unknown))}")
        selected_ids = requested
    selected_mutations = tuple(
        mutation for mutation in mutations if mutation.mutation_id in selected_ids
    )

    work = _RunBudget(max_runs)
    plan: CampaignPlan | None = None
    budget_seconds = 0.0
    compilation_errors: dict[str, str] = {}
    baseline_run = _run_pytest_in_copy(
        boundary,
        mutated_source=None,
        timeout_seconds=baseline_timeout_seconds,
        max_cases=max_cases,
        work=work,
        purpose="baseline",
    )
    baseline = BaselineRun(
        exit_code=baseline_run.exit_code,
        duration_seconds=baseline_run.duration_seconds,
        timed_out=baseline_run.timed_out,
        output=baseline_run.output,
        command=baseline_run.command,
        obligations=baseline_run.obligations,
        evidence_error=_evidence_problem(baseline_run, max_cases=max_cases),
    )

    def finish(
        runs: tuple[MutantRun, ...], canary: CanaryRun | None = None
    ) -> MutationResult:
        selected_runs = {run.mutation.mutation_id: run for run in runs}
        runs = tuple(
            selected_runs[mutation.mutation_id]
            if mutation.mutation_id in selected_ids
            else MutantRun(
                mutation,
                MutationOutcome.EXCLUDED,
                "excluded from the selected scope; no evidence established",
            )
            for mutation in mutations
        )
        runs, stale = apply_equivalence_ledger(runs, ledger)
        return MutationResult(
            laws=str(laws_path),
            implementation=str(implementation_path),
            module_name=boundary.module_name,
            boundary_kind="package" if boundary.is_package else "directory",
            max_cases=max_cases,
            baseline=baseline,
            runs=runs,
            stale_ledger_entries=stale,
            budget_seconds=budget_seconds,
            timeout_multiplier=timeout_multiplier,
            timeout_floor_seconds=timeout_floor_seconds,
            canary=canary,
            plan=plan,
            plan_only=plan_only,
            max_runs=work.max_runs,
            runs_started=work.runs_started,
            stopped_before=work.stopped_before,
        )

    def unrun(mutation: Mutation, reason: str) -> MutantRun:
        problem = compilation_errors.get(mutation.mutation_id)
        if problem is not None:
            return MutantRun(
                mutation, MutationOutcome.INVALID, f"mutant does not compile: {problem}"
            )
        return MutantRun(mutation, MutationOutcome.NOT_RUN, f"not run: {reason}")

    if not baseline.valid:
        return finish(
            tuple(
                unrun(mutation, "baseline was invalid")
                for mutation in selected_mutations
            )
        )

    # Compilation has no child process. Check once so planning and execution
    # agree about which generated candidates can consume a run.
    for mutation in selected_mutations:
        try:
            compile(mutation.mutated_source, f"<mutant {mutation.mutation_id}>", "exec")
        except SyntaxError as error:
            compilation_errors[mutation.mutation_id] = str(error)

    runnable = len(selected_mutations) - len(compilation_errors)
    preparation_runs = 1 + bool(selected_mutations)
    totals = tuple(law.cases_total for law in baseline.obligations)
    cases_per_run = (
        None
        if any(total is None for total in totals)
        else sum(total for total in totals if total is not None)
    )
    maximum_runs = preparation_runs + 3 * runnable
    plan = CampaignPlan(
        generated_mutations=len(mutations),
        excluded_mutations=len(mutations) - len(selected_mutations),
        runnable_mutations=runnable,
        law_cases=len(baseline.obligations),
        cases_per_run=cases_per_run,
        min_runs=preparation_runs + runnable,
        max_runs=maximum_runs,
        max_case_attempts=None
        if cases_per_run is None
        else cases_per_run * maximum_runs,
    )
    budget_seconds = max(
        timeout_floor_seconds, timeout_multiplier * baseline.duration_seconds
    )
    if plan_only:
        return finish(
            tuple(unrun(mutation, "plan only") for mutation in selected_mutations)
        )

    # The mandatory canary establishes wiring before any candidate is scored.
    canary: CanaryRun | None = None
    if selected_mutations:
        try:
            canary_run = _run_pytest_in_copy(
                boundary,
                mutated_source=poison_source(implementation_source),
                timeout_seconds=budget_seconds,
                max_cases=max_cases,
                work=work,
                purpose="canary",
            )
        except _RunLimitReached as error:
            return finish(
                tuple(unrun(mutation, str(error)) for mutation in selected_mutations)
            )
        if canary_run.timed_out:
            canary_problem = None
            canary_note = f"calibrated timeout after {budget_seconds:.1f}s"
        else:
            canary_problem = _evidence_problem(
                canary_run,
                expected=baseline.obligations,
                require_retained=False,
                max_cases=max_cases,
            )
            canary_note = canary_problem or (
                _unwired_note(boundary)
                if canary_run.exit_code == 0
                else f"canary run exit code {canary_run.exit_code}"
            )
        canary = CanaryRun(
            exit_code=canary_run.exit_code,
            timed_out=canary_run.timed_out,
            duration_seconds=canary_run.duration_seconds,
            note=canary_note,
            output=canary_run.output,
            obligations=canary_run.obligations,
            evidence=canary_run.evidence,
            evidence_error=canary_problem,
        )
        if canary.wired is not True:
            return finish(
                tuple(
                    unrun(mutation, "canary did not establish wiring")
                    for mutation in selected_mutations
                ),
                canary,
            )

    finished: list[MutantRun] = []
    for mutation in selected_mutations:
        if mutation.mutation_id in compilation_errors:
            finished.append(unrun(mutation, "does not compile"))
            continue
        if work.stopped_before is not None:
            finished.append(
                unrun(mutation, f"campaign stopped before {work.stopped_before}")
            )
            continue
        try:
            run = _run_one_mutant(
                boundary,
                mutation,
                budget_seconds=budget_seconds,
                baseline_duration=baseline.duration_seconds,
                timeout_multiplier=timeout_multiplier,
                expected=baseline.obligations,
                max_cases=max_cases,
                work=work,
            )
        except _RunLimitReached as error:
            finished.append(unrun(mutation, str(error)))
            continue
        if run.outcome is MutationOutcome.KILLED:
            run = _confirm_kill(
                boundary,
                mutation,
                run,
                budget_seconds=budget_seconds,
                baseline_duration=baseline.duration_seconds,
                timeout_multiplier=timeout_multiplier,
                timeout_floor_seconds=timeout_floor_seconds,
                baseline_timeout_seconds=baseline_timeout_seconds,
                expected=baseline.obligations,
                max_cases=max_cases,
                work=work,
            )
        finished.append(run)
    return finish(tuple(finished), canary)
