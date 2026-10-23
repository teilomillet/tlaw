"""AST mutation sites: the pure generation half of the mutation engine.

Every generated mutation is one small, deterministic, single-site change to an
abstract syntax tree: an arithmetic operator swap, a comparison boundary, a
returned constant, a skipped call, or a negated condition. This module owns
that pure half — which nodes are sites, how a replacement is built, how an ID
commits to content rather than line numbers, generation, and the poisoned
source used by the canary. It has no subprocesses, filesystem, or campaign
state.

``tlaw.mutation`` keeps the campaign engine that runs these candidates; it
re-exports the names defined here so callers keep one import surface.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


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


__all__ = ["Mutation", "generate_mutations", "poison_source"]
