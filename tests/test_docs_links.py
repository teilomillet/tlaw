"""Mechanical guards against documentation link drift.

Documentation is written by hand, so a moved file or a renamed heading can
leave a link that silently points nowhere. These tests are intentionally
narrow: they check that every relative Markdown link resolves to a path in
the repository, that every fragment names a real heading, and that the
absolute `github.com/teilomillet/tlaw` links used across the README resolve
to the same files as a relative link would. Nothing here validates prose or
fetches a network URL; external links stay the reviewer's job.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Directories that never contain documentation meant for readers. Leading-dot
# directories (.git, .venv, .pytest_cache, .hypothesis, ...) are excluded by
# the name check below.
EXCLUDED_DIRECTORIES = {"dist", "build", "node_modules", "__pycache__"}

FENCE_PREFIXES = ("```", "~~~")

# A Markdown inline link or image: [text](target) / ![alt](target).
INLINE_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*(?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)"
)
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")
SELF_REPOSITORY_URL_RE = re.compile(
    r"https://github\.com/teilomillet/tlaw/(?:blob|tree)/main/(?P<path>.+)\Z"
)


def _markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        files.append(path)
    return sorted(files)


def _slug(heading: str) -> str:
    """Approximate the anchor GitHub generates for one heading line."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").strip().lower()
    kept = "".join(char for char in text if char.isalnum() or char in " -_")
    return kept.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match is None:
            continue
        base = _slug(match.group("text"))
        count = occurrences.get(base, 0)
        occurrences[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _links(path: Path) -> list[tuple[int, str]]:
    """Return (line number, target) for each inline link outside code fences."""
    links: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(FENCE_PREFIXES):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        without_code = INLINE_CODE_RE.sub("", line)
        for match in INLINE_LINK_RE.finditer(without_code):
            links.append((number, match.group("target")))
    return links


def _check_target(source: Path, line: int, target: str, problems: list[str]) -> None:
    target = target.strip()
    if not target or target.startswith("mailto:"):
        return

    if target.startswith(("http://", "https://")):
        self_match = SELF_REPOSITORY_URL_RE.match(target)
        if self_match is None:
            return
        target = self_match.group("path")

    path_text, separator, fragment = target.partition("#")

    if path_text:
        resolved = (source.parent / path_text).resolve()
        if not resolved.exists():
            problems.append(
                f"{source.relative_to(ROOT)}:{line}: link target does not exist: "
                f"{target}"
            )
            return
        anchor_file = resolved if resolved.suffix == ".md" else None
    else:
        anchor_file = source

    if not separator or not fragment or anchor_file is None:
        return
    if fragment not in _anchors(anchor_file):
        problems.append(
            f"{source.relative_to(ROOT)}:{line}: no heading anchor "
            f"'{fragment}' in {anchor_file.relative_to(ROOT)}"
        )


def test_every_markdown_link_resolves() -> None:
    files = _markdown_files()
    # A broken discovery walk would make the whole guard vacuous.
    assert ROOT / "README.md" in files
    assert ROOT / "docs" / "current-behavior.md" in files
    assert ROOT / "docs" / "README.md" in files

    problems: list[str] = []
    for path in files:
        for line, target in _links(path):
            _check_target(path, line, target, problems)

    assert not problems, "unresolved documentation links:\n" + "\n".join(problems)
