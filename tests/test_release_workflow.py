"""Release and CI workflow invariants that protect published artifacts."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _release_workflow() -> str:
    return RELEASE_WORKFLOW.read_text(encoding="utf-8")


def test_release_runs_the_complete_gate_before_building_artifacts() -> None:
    workflow = _release_workflow()

    gate = workflow.index("run: ./scripts/gate")
    build = workflow.index("run: uv build --clear")
    publish = workflow.index("run: uv publish --trusted-publishing always")

    assert workflow.count("run: ./scripts/gate") == 1
    assert gate < build < publish
    assert "Run the complete repository gate" in workflow


def test_release_attests_the_verified_distributions_before_upload() -> None:
    workflow = _release_workflow()

    download = workflow.index("uses: actions/download-artifact@")
    attest = workflow.index("uses: astral-sh/attest-action@")
    publish = workflow.index("run: uv publish --trusted-publishing always")

    # PEP 740: the attestations must describe the downloaded, verified files,
    # and uv must see them before it uploads.
    assert download < attest < publish
    assert re.search(r"uses: astral-sh/attest-action@[0-9a-f]{40}\b", workflow)
    assert "paths: dist/*" in workflow
    assert "id-token: write" in workflow


def test_release_creates_a_tag_and_github_release_from_the_changelog() -> None:
    workflow = _release_workflow()

    assert 'tag="v${VERSION}"' in workflow
    assert "gh release create" in workflow
    assert '--target "$GITHUB_SHA"' in workflow
    assert "--notes-file release-notes.md" in workflow
    assert "CHANGELOG.md" in workflow
    assert "contents: write" in workflow


def test_release_upload_and_github_release_are_rerunnable() -> None:
    workflow = _release_workflow()

    assert "--check-url https://pypi.org/simple/" in workflow
    assert "already on PyPI" in workflow
    assert "gh release view" in workflow
    assert "gh release upload" in workflow
    assert "--clobber" in workflow


def test_release_supports_a_manual_preflight_and_recovery_publish() -> None:
    workflow = _release_workflow()

    assert "workflow_dispatch:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "DISPATCH_PUBLISH" in workflow
    assert "manual dispatch:" in workflow
    # The publish job is gated by both the version decision and the
    # explicit dispatch input, so a preflight never uploads.
    assert (
        "if: needs.version.outputs.changed == 'true' && "
        "needs.version.outputs.publish == 'true'"
    ) in workflow


def test_ci_runs_the_gate_on_every_supported_python_version() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "matrix:" in workflow
    assert '"3.12"' in workflow
    assert '"3.13"' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    assert "timeout-minutes:" in workflow
