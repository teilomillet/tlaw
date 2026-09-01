"""Release workflow invariants that protect published artifacts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_runs_the_complete_gate_before_building_artifacts() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    gate = workflow.index("run: ./scripts/gate")
    build = workflow.index("run: uv build --clear")
    publish = workflow.index("run: uv publish --trusted-publishing always")

    assert workflow.count("run: ./scripts/gate") == 1
    assert gate < build < publish
    assert "Run the complete repository gate" in workflow
