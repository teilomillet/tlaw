"""Repository invariants for independent and self-hosted evidence layers."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_push_gate_runs_symbolic_contracts_and_fast_self_laws() -> None:
    gate = (ROOT / "scripts" / "gate").read_text(encoding="utf-8")

    assert gate.count("./scripts/crosshair") == 1
    assert gate.count("uv run tlaw check self_laws/") == 1
    assert gate.index("./scripts/crosshair") < gate.index(
        "uv run tlaw check self_laws/"
    )


def test_core_evidence_mutates_each_declared_core_boundary_once() -> None:
    evidence = (ROOT / "scripts" / "core-evidence").read_text(encoding="utf-8")

    assert evidence.count("tlaw/comparison.py") == 1
    assert evidence.count("tlaw/exclusion.py") == 1
    assert evidence.count("tlaw/domains.py") == 1
    assert evidence.count("uv run tlaw mutate") == 3


def test_core_evidence_workflow_runs_the_shared_script() -> None:
    workflow = (ROOT / ".github" / "workflows" / "core-evidence.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count("run: ./scripts/core-evidence") == 1
    assert '"tlaw/mutation.py"' in workflow
    assert '"pyproject.toml"' in workflow
    assert '"uv.lock"' in workflow
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
