"""Static law-space differences between two Git revisions.

``tlaw diff`` is a review view over Git.  It reads source blobs directly from
the object database and parses them with :mod:`ast`; historical code is never
checked out, imported, or executed.
"""

from __future__ import annotations

import ast
import copy
import io
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .audit import ManifestError


DomainRelation = Literal["same", "narrowed", "widened", "changed"]


@dataclass(frozen=True)
class LawChange:
    """One visible change to an accepted law."""

    identity: str
    classifications: tuple[str, ...]

    def render(self) -> str:
        return f"{self.identity}: {', '.join(self.classifications)}"


@dataclass(frozen=True)
class LawDiffResult:
    """The law-space changes between two resolved Git commits."""

    base: str
    head: str
    changes: tuple[LawChange, ...]

    @property
    def exit_code(self) -> int:
        """Return 0 for no changes and 1 when reviewable changes exist."""
        return 1 if self.changes else 0

    def render_text(self) -> str:
        lines = [f"tlaw law-space diff {self.base[:12]}..{self.head[:12]}"]
        if not self.changes:
            lines.append("No law changes.")
            return "\n".join(lines)
        lines.append("Law changes:")
        lines.extend(f"- {change.render()}" for change in self.changes)
        lines.append(f"Changed laws: {len(self.changes)}")
        return "\n".join(lines)


@dataclass(frozen=True)
class _Law:
    identity: str
    description: str
    domains: tuple[tuple[str, ast.AST], ...]
    generation: str
    decorators: tuple[str, ...]
    body: tuple[str, ...]


@dataclass(frozen=True)
class _Range:
    kind: str
    low: int | float
    high: int | float


@dataclass(frozen=True)
class _Finite:
    values: frozenset[str]


@dataclass(frozen=True)
class _ListDomain:
    element: object
    minimum: int
    maximum: int | None


@dataclass(frozen=True)
class _RecordDomain:
    constructor: str | None
    fields: tuple[tuple[str, object], ...]


def _run_git(repository: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise ManifestError(f"could not run git: {error}") from error
    if completed.returncode != 0:
        message = completed.stderr.decode(errors="replace").strip()
        detail = message or f"git {' '.join(arguments)} exited {completed.returncode}"
        raise ManifestError(detail)
    return completed.stdout


def _repository_root(repository: str | Path) -> Path:
    candidate = Path(repository).resolve()
    output = _run_git(candidate, "rev-parse", "--show-toplevel")
    return Path(output.decode(errors="replace").strip()).resolve()


def _split_range(revisions: str) -> tuple[str, str]:
    if "..." in revisions or revisions.count("..") != 1:
        raise ManifestError(
            "diff needs one two-dot Git range such as origin/main..HEAD"
        )
    base, head = revisions.split("..", 1)
    if not base or not head:
        raise ManifestError("diff needs both revisions, such as origin/main..HEAD")
    return base, head


def _resolve_revision(repository: Path, revision: str) -> str:
    output = _run_git(
        repository,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}^{{commit}}",
    )
    resolved = output.decode(errors="replace").strip()
    if len(resolved) != 40:
        raise ManifestError(f"git did not resolve {revision!r} to one commit")
    return resolved


def _python_paths(repository: Path, revision: str) -> tuple[str, ...]:
    output = _run_git(repository, "ls-tree", "-r", "-z", "--name-only", revision)
    paths = (
        value.decode(errors="surrogateescape") for value in output.split(b"\0") if value
    )
    return tuple(sorted(path for path in paths if path.endswith(".py")))


def _source_blob(repository: Path, revision: str, path: str) -> str:
    blob = _run_git(repository, "cat-file", "blob", f"{revision}:{path}")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(blob).readline)
        return blob.decode(encoding)
    except (LookupError, SyntaxError, UnicodeDecodeError) as error:
        raise ManifestError(
            f"could not decode Python source at {revision[:12]}:{path}: {error}"
        ) from error


def _tlaw_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    laws: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for imported in statement.names:
                if imported.name == "tlaw":
                    modules.add(imported.asname or imported.name)
                elif imported.name == "tlaw.authoring" and imported.asname:
                    modules.add(imported.asname)
        elif isinstance(statement, ast.ImportFrom):
            if statement.module in {"tlaw", "tlaw.authoring"}:
                for imported in statement.names:
                    if imported.name == "law":
                        laws.add(imported.asname or imported.name)
    return modules, laws


def _assignments(tree: ast.Module) -> dict[str, ast.AST]:
    values: dict[str, ast.AST] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            if statement.value is not None:
                values[statement.target.id] = statement.value
        elif isinstance(statement, ast.TypeAlias) and isinstance(
            statement.name, ast.Name
        ):
            values[statement.name.id] = statement.value
    return values


class _AliasExpander(ast.NodeTransformer):
    def __init__(self, assignments: dict[str, ast.AST]) -> None:
        self.assignments = assignments
        self.active: set[str] = set()

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if not isinstance(node.ctx, ast.Load) or node.id not in self.assignments:
            return node
        if node.id in self.active:
            return node
        self.active.add(node.id)
        try:
            expanded = copy.deepcopy(self.assignments[node.id])
            return self.visit(expanded)
        finally:
            self.active.remove(node.id)


def _expanded(node: ast.AST, assignments: dict[str, ast.AST]) -> ast.AST:
    return _AliasExpander(assignments).visit(copy.deepcopy(node))


def _is_law_reference(node: ast.AST, *, modules: set[str], laws: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in laws
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "law"
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def _law_decorator(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    modules: set[str],
    laws: set[str],
) -> ast.AST | None:
    for decorator in function.decorator_list:
        reference = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _is_law_reference(reference, modules=modules, laws=laws):
            return decorator
    return None


def _arguments(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    arguments = function.args
    values = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    ]
    if arguments.vararg is not None:
        values.append(arguments.vararg)
    if arguments.kwarg is not None:
        values.append(arguments.kwarg)
    return tuple(values)


def _description(function_name: str, decorator: ast.AST) -> str:
    if not isinstance(decorator, ast.Call):
        return function_name.replace("_", " ")
    if not decorator.args:
        return function_name.replace("_", " ")
    value = decorator.args[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return ast.dump(value, include_attributes=False)


def _domains(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator: ast.AST,
    assignments: dict[str, ast.AST],
) -> tuple[tuple[str, ast.AST], ...]:
    if isinstance(decorator, ast.Call):
        return tuple(
            (
                keyword.arg if keyword.arg is not None else "**",
                _expanded(keyword.value, assignments),
            )
            for keyword in decorator.keywords
        )
    return tuple(
        (
            argument.arg,
            _expanded(argument.annotation, assignments),
        )
        for argument in _arguments(function)
        if argument.annotation is not None
    )


def _body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    statements = list(function.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements.pop(0)
    return tuple(ast.dump(value, include_attributes=False) for value in statements)


def _laws_in_source(source: str, *, revision: str, path: str) -> tuple[_Law, ...]:
    # Every supported decorator spelling imports ``tlaw``. Skipping files
    # without that token prevents unrelated historical syntax from blocking a
    # law review while still parsing every possible tlaw law file.
    if "tlaw" not in source:
        return ()
    try:
        tree = ast.parse(source, filename=f"{revision[:12]}:{path}")
    except SyntaxError as error:
        raise ManifestError(
            f"could not parse Python source at {revision[:12]}:{path}: "
            f"{error.msg} (line {error.lineno})"
        ) from error
    modules, law_names = _tlaw_imports(tree)
    if not modules and not law_names:
        return ()
    assignments = _assignments(tree)
    found = []
    for statement in tree.body:
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        decorator = _law_decorator(
            statement,
            modules=modules,
            laws=law_names,
        )
        if decorator is None:
            continue
        found.append(
            _Law(
                identity=f"{path}::{statement.name}",
                description=_description(statement.name, decorator),
                domains=_domains(statement, decorator, assignments),
                generation=(
                    "explicit" if isinstance(decorator, ast.Call) else "inferred"
                ),
                decorators=tuple(
                    ast.dump(value, include_attributes=False)
                    for value in statement.decorator_list
                    if value is not decorator
                ),
                body=_body(statement),
            )
        )
    return tuple(found)


def _laws_at(repository: Path, revision: str) -> dict[str, _Law]:
    found: dict[str, _Law] = {}
    for path in _python_paths(repository, revision):
        source = _source_blob(repository, revision, path)
        for law in _laws_in_source(source, revision=revision, path=path):
            found[law.identity] = law
    return found


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_argument(node: ast.Call, position: int, name: str) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg == name),
        None,
    )


def _literal_number(node: ast.AST) -> int | float | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _subscript_parts(node: ast.Subscript) -> tuple[ast.AST, ...]:
    if isinstance(node.slice, ast.Tuple):
        return tuple(node.slice.elts)
    return (node.slice,)


def _domain_model(node: ast.AST) -> object | None:
    if isinstance(node, ast.Name) and node.id == "bool":
        return _Finite(frozenset((repr(False), repr(True))))

    if isinstance(node, ast.Subscript):
        name = _call_name(node.value)
        parts = _subscript_parts(node)
        if name == "Annotated" and len(parts) == 2:
            metadata = _domain_model(parts[1])
            if metadata is not None:
                return (
                    "annotated",
                    ast.dump(parts[0], include_attributes=False),
                    metadata,
                )
        if name == "Literal" and parts:
            values = []
            for part in parts:
                try:
                    value = ast.literal_eval(part)
                except (ValueError, TypeError, SyntaxError):
                    return None
                values.append(repr(value))
            return _Finite(frozenset(values))
        if name in {"list", "List"} and len(parts) == 1:
            element = _domain_model(parts[0])
            if element is None:
                element = ("plain", ast.dump(parts[0], include_attributes=False))
            return _ListDomain(element=element, minimum=0, maximum=None)

    if not isinstance(node, ast.Call):
        return None
    name = _call_name(node.func)
    if name in {"integer", "number"}:
        low_node = _call_argument(node, 0, "low")
        high_node = _call_argument(node, 1, "high")
        if low_node is None or high_node is None:
            return None
        low = _literal_number(low_node)
        high = _literal_number(high_node)
        if low is not None and high is not None:
            return _Range(kind=name, low=low, high=high)
        return None
    if name == "one_of":
        values_node = _call_argument(node, 0, "values")
        if values_node is None:
            return None
        try:
            values = ast.literal_eval(values_node)
        except (ValueError, TypeError, SyntaxError):
            return None
        if not isinstance(values, list | tuple | set | frozenset):
            return None
        return _Finite(frozenset(repr(value) for value in values))
    if name == "record":
        if (
            len(node.args) > 1
            or not node.keywords
            or any(keyword.arg is None for keyword in node.keywords)
        ):
            return None
        constructor = (
            ast.dump(node.args[0], include_attributes=False) if node.args else None
        )
        fields = []
        for keyword in node.keywords:
            assert keyword.arg is not None
            field_model = _domain_model(keyword.value)
            if field_model is None:
                field_model = (
                    "plain",
                    ast.dump(keyword.value, include_attributes=False),
                )
            fields.append((keyword.arg, field_model))
        return _RecordDomain(
            constructor=constructor,
            fields=tuple(sorted(fields)),
        )
    if name == "list_of":
        element_node = _call_argument(node, 0, "element")
        if element_node is None:
            return None
        element = _domain_model(element_node)
        if element is None:
            element = ("plain", ast.dump(element_node, include_attributes=False))
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        if "min_size" in keywords:
            try:
                minimum = ast.literal_eval(keywords["min_size"])
            except (ValueError, TypeError, SyntaxError):
                return None
            if isinstance(minimum, bool) or not isinstance(minimum, int):
                return None
        else:
            minimum = 0
        if "max_size" in keywords:
            try:
                maximum = ast.literal_eval(keywords["max_size"])
            except (ValueError, TypeError, SyntaxError):
                return None
            if maximum is not None and (
                isinstance(maximum, bool) or not isinstance(maximum, int)
            ):
                return None
        else:
            maximum = None
        return _ListDomain(element=element, minimum=minimum, maximum=maximum)
    return None


def _interval_relation(
    old_low: int | float,
    old_high: int | float | None,
    new_low: int | float,
    new_high: int | float | None,
) -> DomainRelation:
    old_upper = float("inf") if old_high is None else old_high
    new_upper = float("inf") if new_high is None else new_high
    new_inside_old = new_low >= old_low and new_upper <= old_upper
    old_inside_new = old_low >= new_low and old_upper <= new_upper
    if new_inside_old and old_inside_new:
        return "same"
    if new_inside_old:
        return "narrowed"
    if old_inside_new:
        return "widened"
    return "changed"


def _combine_relations(*relations: DomainRelation) -> DomainRelation:
    active = {value for value in relations if value != "same"}
    if not active:
        return "same"
    if active == {"narrowed"}:
        return "narrowed"
    if active == {"widened"}:
        return "widened"
    return "changed"


def _model_relation(old: object, new: object) -> DomainRelation:
    if old == new:
        return "same"
    if isinstance(old, _Range) and isinstance(new, _Range):
        if old.kind != new.kind:
            return "changed"
        return _interval_relation(old.low, old.high, new.low, new.high)
    if isinstance(old, _Finite) and isinstance(new, _Finite):
        if new.values < old.values:
            return "narrowed"
        if old.values < new.values:
            return "widened"
        return "changed"
    if isinstance(old, _ListDomain) and isinstance(new, _ListDomain):
        return _combine_relations(
            _model_relation(old.element, new.element),
            _interval_relation(
                old.minimum,
                old.maximum,
                new.minimum,
                new.maximum,
            ),
        )
    if isinstance(old, _RecordDomain) and isinstance(new, _RecordDomain):
        if old.constructor != new.constructor:
            return "changed"
        old_fields = dict(old.fields)
        new_fields = dict(new.fields)
        if old_fields.keys() != new_fields.keys():
            return "changed"
        return _combine_relations(
            *(
                _model_relation(old_fields[field_name], new_fields[field_name])
                for field_name in old_fields
            )
        )
    if (
        isinstance(old, tuple)
        and isinstance(new, tuple)
        and len(old) == len(new) == 3
        and old[0] == new[0] == "annotated"
    ):
        if old[1] != new[1]:
            return "changed"
        return _model_relation(old[2], new[2])
    return "changed"


def _node_relation(old: ast.AST, new: ast.AST) -> DomainRelation:
    if ast.dump(old, include_attributes=False) == ast.dump(
        new, include_attributes=False
    ):
        return "same"
    old_model = _domain_model(old)
    new_model = _domain_model(new)
    if old_model is None or new_model is None:
        return "changed"
    return _model_relation(old_model, new_model)


def _domains_relation(
    old: tuple[tuple[str, ast.AST], ...],
    new: tuple[tuple[str, ast.AST], ...],
) -> DomainRelation:
    old_by_name = dict(old)
    new_by_name = dict(new)
    if old_by_name.keys() != new_by_name.keys():
        return "changed"
    return _combine_relations(
        *(_node_relation(old_by_name[name], new_by_name[name]) for name in old_by_name)
    )


def _changes(old: dict[str, _Law], new: dict[str, _Law]) -> tuple[LawChange, ...]:
    changed = []
    for identity in sorted(old.keys() | new.keys()):
        before = old.get(identity)
        after = new.get(identity)
        if before is None:
            changed.append(LawChange(identity, ("added",)))
            continue
        if after is None:
            changed.append(LawChange(identity, ("removed",)))
            continue
        classifications = []
        if before.description != after.description:
            classifications.append("description changed")
        relation = _domains_relation(before.domains, after.domains)
        if relation != "same":
            classifications.append(f"domain {relation}")
        if before.generation != after.generation:
            classifications.append("generation changed")
        if before.decorators != after.decorators:
            classifications.append("decorators changed")
        if before.body != after.body:
            classifications.append("body changed")
        if classifications:
            changed.append(LawChange(identity, tuple(classifications)))
    return tuple(changed)


def diff(
    revisions: str,
    *,
    repository: str | Path = ".",
) -> LawDiffResult:
    """Report static law changes across ``BASE..HEAD`` without executing code."""
    root = _repository_root(repository)
    base_name, head_name = _split_range(revisions)
    base = _resolve_revision(root, base_name)
    head = _resolve_revision(root, head_name)
    return LawDiffResult(
        base=base,
        head=head,
        changes=_changes(_laws_at(root, base), _laws_at(root, head)),
    )


__all__ = ["LawChange", "LawDiffResult", "diff"]
