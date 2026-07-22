"""Executable Python audit specifications."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .audit import (
    AuditManifest,
    Candidate,
    ManifestError,
    _validated_manifest,
)


def _target_reference(implementation: object) -> str:
    if isinstance(implementation, str):
        if not implementation.strip():
            raise ManifestError("an implementation reference cannot be empty")
        return implementation

    module = getattr(implementation, "__module__", None)
    qualified_name = getattr(implementation, "__qualname__", None)
    if (
        not isinstance(module, str)
        or not isinstance(qualified_name, str)
        or "<locals>" in qualified_name
    ):
        raise ManifestError(
            "an implementation must come from an importable module, not local scope"
        )
    return f"{module}:{qualified_name}"


class Lawbook:
    """A code-first declaration of tests and implementations to audit."""

    def __init__(
        self,
        source: str | Path,
        *,
        tests: tuple[str, ...] | None = None,
        pytest_args: tuple[str, ...] = ("-q",),
        timeout_seconds: float = 30.0,
    ) -> None:
        self.source = Path(source).resolve()
        self.tests = tests or (self.source.name,)
        self.pytest_args = pytest_args
        self.timeout_seconds = timeout_seconds
        self._baseline: Candidate | None = None
        self._known_wrong: list[Candidate] = []

    def baseline(self, name: str, implementation: object) -> None:
        """Declare the implementation that all laws must accept."""
        if self._baseline is not None:
            raise ManifestError("a lawbook can declare only one baseline")
        self._baseline = Candidate(name=name, target=_target_reference(implementation))

    def known_wrong(
        self,
        name: str,
        implementation: object,
        *,
        breaks: str,
    ) -> None:
        """Declare an implementation the laws are expected to reject."""
        self._known_wrong.append(
            Candidate(
                name=name,
                target=_target_reference(implementation),
                breaks=breaks,
            )
        )

    def to_manifest(self) -> AuditManifest:
        """Validate this code declaration and freeze it for the audit runner."""
        if self._baseline is None:
            raise ManifestError("lawbook must declare one baseline implementation")
        return _validated_manifest(
            path=self.source,
            tests=self.tests,
            pytest_args=self.pytest_args,
            timeout_seconds=self.timeout_seconds,
            baseline=self._baseline,
            known_wrong=tuple(self._known_wrong),
        )


def _module_file(module: ModuleType) -> Path | None:
    filename = getattr(module, "__file__", None)
    if not isinstance(filename, str):
        return None
    try:
        return Path(filename).resolve()
    except OSError:
        return None


def load_lawbook(path: str | Path) -> Lawbook:
    """Execute a local laws.py file and return its single Lawbook declaration."""
    law_path = Path(path).resolve()
    if not law_path.is_file():
        raise ManifestError(f"law file does not exist: {law_path}")

    digest = hashlib.sha256(str(law_path).encode()).hexdigest()[:12]
    module_name = f"_tlaw_lawbook_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, law_path)
    if spec is None or spec.loader is None:
        raise ManifestError(f"cannot load Python law file: {law_path}")

    module = importlib.util.module_from_spec(spec)
    modules_before = set(sys.modules)
    sys.modules[module_name] = module
    sys.path.insert(0, str(law_path.parent))
    try:
        spec.loader.exec_module(module)
    except ManifestError:
        raise
    except Exception as error:
        raise ManifestError(f"cannot execute {law_path}: {error}") from error
    finally:
        sys.path.pop(0)
        for imported_name in set(sys.modules) - modules_before:
            imported = sys.modules.get(imported_name)
            imported_path = _module_file(imported) if imported is not None else None
            if imported_name == module_name or (
                imported_path is not None
                and imported_path.is_relative_to(law_path.parent)
            ):
                sys.modules.pop(imported_name, None)

    declarations = {
        id(value): value
        for value in vars(module).values()
        if isinstance(value, Lawbook)
    }
    if len(declarations) != 1:
        raise ManifestError(
            f"{law_path} must define exactly one Lawbook; found {len(declarations)}"
        )
    lawbook = next(iter(declarations.values()))
    if lawbook.source != law_path:
        raise ManifestError("Lawbook source must be __file__ for the loaded law file")
    return lawbook
