"""Laws for the package mutation boundary.

Validated against kvfit before implementation
(docs/package-boundary-probe.md); pinned here with a self-contained
package so the suite needs no external project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tlaw.audit import ManifestError
from tlaw.mutation import MutationOutcome, describe_boundary, mutate


HELPER = """\
def step():
    return 2
"""

TARGET = """\
from shapes.constants import step


def stretch(values):
    return [value * step() for value in values]
"""

LAWS = """\
import tlaw

from shapes.stretch import stretch


@tlaw.law
def stretching_doubles_every_value(values: list[int]):
    return stretch(values) == [value * 2 for value in values]
"""


def _build_package(root: Path) -> tuple[Path, Path]:
    """A package whose target module imports a sibling by dotted name."""
    package = root / "shapes"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "constants.py").write_text(HELPER, encoding="utf-8")
    target = package / "stretch.py"
    target.write_text(TARGET, encoding="utf-8")

    laws_directory = root / "laws"
    laws_directory.mkdir()
    laws = laws_directory / "laws.py"
    laws.write_text(LAWS, encoding="utf-8")
    return laws, target


def test_a_package_module_is_described_by_its_dotted_name(tmp_path: Path) -> None:
    laws, target = _build_package(tmp_path)

    boundary = describe_boundary(laws, target)

    assert boundary.is_package is True
    assert boundary.module_name == "shapes.stretch"
    assert boundary.source == tmp_path / "shapes"
    assert boundary.laws_directory == laws.parent


def test_a_nested_package_keeps_its_full_dotted_name(tmp_path: Path) -> None:
    package = tmp_path / "outer"
    inner = package / "inner"
    inner.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (inner / "__init__.py").write_text("", encoding="utf-8")
    module = inner / "deep.py"
    module.write_text("def f():\n    return 1\n", encoding="utf-8")
    laws = tmp_path / "laws.py"
    laws.write_text("", encoding="utf-8")

    boundary = describe_boundary(laws, module)

    # The outermost package is the boundary, so intra-package imports
    # resolve inside one copied world.
    assert boundary.source == package
    assert boundary.module_name == "outer.inner.deep"


def test_the_flat_boundary_still_requires_one_directory(tmp_path: Path) -> None:
    laws = tmp_path / "laws.py"
    laws.write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    implementation = elsewhere / "implementation.py"
    implementation.write_text("", encoding="utf-8")

    with pytest.raises(ManifestError) as refusal:
        describe_boundary(laws, implementation)

    assert "share one directory" in str(refusal.value)


def test_mutating_a_package_module_kills_its_mutants(tmp_path: Path) -> None:
    laws, target = _build_package(tmp_path)

    result = mutate(laws, target)

    assert result.baseline_valid
    # The canary proves the copied package shadowed anything installed.
    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert result.survived_count == 0
    assert result.has_errors is False
    # Reports name the module by its dotted path, not a bare stem.
    assert all(run.mutation.qualname.startswith("stretch") for run in result.runs)
    assert "shapes.stretch" in result.render_text()
    # Killing laws are named by the clean, portable basename, not by a
    # temp-workspace relative climb full of `../`.
    killed = [run for run in result.runs if run.failed_tests]
    assert killed
    for run in killed:
        for test in run.failed_tests:
            assert test.startswith("laws.py::")
            assert ".." not in test
    # Numeric plan ranges may contain ".."; relative path climbs must not.
    assert "../" not in result.render_text()
    assert "..\\" not in result.render_text()


def test_project_root_laws_still_observe_the_copied_package(tmp_path: Path) -> None:
    # Regression: when laws.py and shapes/ were siblings, Python placed the
    # original project root ahead of PYTHONPATH. Baseline passed against the
    # original package and the mandatory canary correctly rejected the run.
    _, target = _build_package(tmp_path)
    laws = tmp_path / "laws.py"
    laws.write_text(LAWS, encoding="utf-8")

    result = mutate(laws, target)

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert result.survived_count == 0
    assert result.has_errors is False


def test_a_package_mutant_is_visible_to_its_sibling_importers(
    tmp_path: Path,
) -> None:
    # The whole package is copied, so a module that imports a sibling
    # sees the mutated world rather than a half-mutated one.
    laws, _ = _build_package(tmp_path)
    constants = tmp_path / "shapes" / "constants.py"

    result = mutate(laws, constants)

    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert any(
        run.outcome is MutationOutcome.KILLED and "step" in run.mutation.description
        for run in result.runs
    )


NS_HELPER = """\
def factor():
    return 4
"""

NS_TARGET = """\
from space.shapes.scale import factor


def stretched(value):
    return value * factor()
"""

NS_LAWS = """\
import tlaw

from space.shapes.geometry import stretched


@tlaw.law
def stretching_multiplies_by_four(value: int):
    return stretched(value) == value * 4
"""


def _build_namespace_package(root: Path) -> tuple[Path, Path]:
    """A PEP 420 namespace package: no __init__.py at any level.

    `space` and `space.shapes` are namespace packages; the modules import
    each other by full dotted name, so the boundary must be discovered
    from the laws' import, not from an __init__.py chain.
    """
    shapes = root / "space" / "shapes"
    shapes.mkdir(parents=True)
    (shapes / "scale.py").write_text(NS_HELPER, encoding="utf-8")
    target = shapes / "geometry.py"
    target.write_text(NS_TARGET, encoding="utf-8")

    laws_directory = root / "laws"
    laws_directory.mkdir()
    laws = laws_directory / "laws.py"
    laws.write_text(NS_LAWS, encoding="utf-8")
    return laws, target


def test_a_namespace_package_is_found_from_the_laws_import(tmp_path: Path) -> None:
    laws, target = _build_namespace_package(tmp_path)

    boundary = describe_boundary(laws, target)

    assert boundary.is_package is True
    assert boundary.module_name == "space.shapes.geometry"
    # The top namespace directory is copied and shadowed, not the leaf.
    assert boundary.source == tmp_path / "space"


def test_mutating_a_namespace_package_module_kills_its_mutants(
    tmp_path: Path,
) -> None:
    laws, target = _build_namespace_package(tmp_path)

    import os

    env_before = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = (
        f"{tmp_path}{os.pathsep}{env_before}" if env_before else str(tmp_path)
    )
    try:
        result = mutate(laws, target)
    finally:
        if env_before is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = env_before

    assert result.baseline_valid
    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert result.survived_count == 0
    assert "space.shapes.geometry" in result.render_text()


def test_a_regular_package_under_a_namespace_parent_keeps_the_full_name(
    tmp_path: Path,
) -> None:
    shapes = tmp_path / "space" / "shapes"
    shapes.mkdir(parents=True)
    (shapes / "__init__.py").write_text("", encoding="utf-8")
    (shapes / "scale.py").write_text(NS_HELPER, encoding="utf-8")
    target = shapes / "geometry.py"
    target.write_text(NS_TARGET, encoding="utf-8")
    laws = tmp_path / "laws.py"
    laws.write_text(NS_LAWS, encoding="utf-8")

    boundary = describe_boundary(laws, target)

    assert boundary.source == tmp_path / "space"
    assert boundary.module_name == "space.shapes.geometry"

    import os

    env_before = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = (
        f"{tmp_path}{os.pathsep}{env_before}" if env_before else str(tmp_path)
    )
    try:
        result = mutate(laws, target)
    finally:
        if env_before is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = env_before

    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert result.survived_count == 0
    assert "space.shapes.geometry" in result.render_text()


def test_a_regular_package_init_under_a_namespace_parent_keeps_the_full_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shapes = tmp_path / "space" / "shapes"
    shapes.mkdir(parents=True)
    target = shapes / "__init__.py"
    target.write_text(
        "def stretched(value):\n    return value * 4\n",
        encoding="utf-8",
    )
    laws = tmp_path / "laws.py"
    laws.write_text(
        """\
import tlaw

from space.shapes import stretched


@tlaw.law
def stretching_multiplies_by_four(value: int):
    return stretched(value) == value * 4
""",
        encoding="utf-8",
    )

    boundary = describe_boundary(laws, target)

    assert boundary.source == tmp_path / "space"
    assert boundary.module_relative == Path("space/shapes/__init__.py")
    assert boundary.module_name == "space.shapes"

    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = mutate(laws, target)

    assert result.canary is not None and result.canary.wired is True
    assert result.killed_count >= 1
    assert result.survived_count == 0
    assert "space.shapes" in result.render_text()


def test_a_flat_import_is_not_mistaken_for_a_namespace_package(
    tmp_path: Path,
) -> None:
    # `from implementation import f` is a bare name, not dotted, so the
    # flat boundary still applies and still requires one shared directory.
    laws = tmp_path / "laws.py"
    laws.write_text("import tlaw\nfrom implementation import f\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    implementation = elsewhere / "implementation.py"
    implementation.write_text("def f():\n    return 1\n", encoding="utf-8")

    with pytest.raises(ManifestError):
        describe_boundary(laws, implementation)
