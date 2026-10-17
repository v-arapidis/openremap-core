"""
Documentation path guard — committed docs must not point at things that are gone.

Why this exists: ``CONTRIBUTING.md`` shipped a complete "Adding a New ECU
Extractor" guide that told contributors to create files under
``src/openremap/tuning/manufacturers/...`` and to import
``openremap.tuning.manufacturers.base``.  Neither had existed since the package
re-layout, so anyone following the guide hit a wall at step 1 — and nothing in
the suite noticed, because a stale path is not a Python error until a human
copies it.  The same rot had already hit the 20 ``_dev/summary`` command
summaries and the two internal roadmaps.

What it checks, in the *committed* markdown that a contributor can actually
read: every local markdown link target, and every backticked repo path (file or
directory) rooted at one of the project's top-level directories.

What it deliberately does not check (point-in-time records — never rewritten):

* ``docs/internal/audits/**`` and ``docs/internal/version-audits/**`` — dated
  findings; the paths they mention were real on that date.
* released ``changelog/<version>.md`` — a release record describes the tree it
  shipped with.  Only the *unreleased* changelog is living, detected by its
  ``— unreleased`` header, so it joins this check automatically while it is
  being written and leaves it when the version ships.
* files that are not committed (``AGENTS.md``, ``agents/**``, ``notes/**``) —
  they are gitignored, so an outside contributor never sees them anyway.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Where a backticked token is treated as "a path that must exist here".  A bare
# filename like `stock.bin` or `recipe.remap` is an example, not a repo path, so
# only paths with a directory component are checked.
PATH_ROOTS = ("openremap/", "tests/", "docs/", "changelog/", "_dev/")
TOP_LEVEL_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "DISCLAIMER.md",
    "THIRD_PARTY.md",
    "pyproject.toml",
    "uv.lock",
}
# Any path with one of these endings and a directory component is checked even
# when its first segment is not a known root: a stale prefix such as the old
# `src/openremap/tuning/...` must not be able to hide behind an unknown root.
# (It did exactly that in the first version of this guard — the bug it was
# written for was invisible to it, and a human found it instead.)
CODE_EXTENSIONS = (".py", ".md", ".toml", ".json", ".rs", ".yml", ".yaml", ".lock")
# Deliberate examples of files a contributor will create; they cannot exist yet.
EXAMPLE_PATHS = {
    "openremap/core/manufacturers/siemens/sid206/extractor.py",
    "openremap/core/manufacturers/<brand>/<family>/extractor.py",
    "tests/tuning/manufacturers/test_<family>_extractor.py",
}
# Files whose whole purpose is to record a moment in time.
POINT_IN_TIME = ("docs/internal/audits/", "docs/internal/version-audits/")

_BACKTICKED = re.compile(r"`([A-Za-z0-9_./-]+/?)`")
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _committed_markdown() -> list[str]:
    """Markdown files tracked by git and present on disk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("git is not available — cannot determine the committed docs")
    return [f for f in out.split() if (REPO_ROOT / f).is_file()]


def _is_living(rel: str) -> bool:
    if rel.startswith(POINT_IN_TIME):
        return False
    if rel.startswith("changelog/"):
        head = (REPO_ROOT / rel).read_text(encoding="utf-8")[:400].lower()
        return "unreleased" in head
    return True


LIVING_DOCS = [f for f in _committed_markdown() if _is_living(f)]


def _missing_paths(rel: str) -> list[str]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    missing: list[str] = []

    for target in sorted(set(_MD_LINK.findall(text))):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        local = target.split("#")[0]
        if not local:
            continue
        # Markdown link targets are relative to the file that contains them.
        if not ((REPO_ROOT / rel).parent / local).exists():
            missing.append(f"markdown link → {target}")

    for token in sorted(set(_BACKTICKED.findall(text))):
        if any(ch in token for ch in "<>*~:"):
            continue
        bare = token.rstrip("/")
        if bare in EXAMPLE_PATHS:
            continue
        rooted = token.startswith(PATH_ROOTS) or bare in TOP_LEVEL_FILES
        path_like = "/" in bare and bare.endswith(CODE_EXTENSIONS)
        if not (rooted or path_like):
            continue
        if not (REPO_ROOT / bare).exists():
            missing.append(f"code path → {token}")

    return missing


class TestDocumentationPaths:
    def test_the_guard_actually_covers_the_docs(self):
        """A guard that silently checks nothing is worse than no guard."""
        assert "README.md" in LIVING_DOCS
        assert "CONTRIBUTING.md" in LIVING_DOCS

    def test_released_changelogs_are_exempt(self):
        """Point-in-time records are excluded on purpose, not by accident.

        Deliberately written against *whichever* changelog is unreleased rather
        than a pinned version: when the unreleased file was renamed from 0.8.0 to
        0.7.7, an assertion of the form "no changelog/0.7* is living" failed for
        the right reason but invited a wrong fix. It is also tolerant of the
        unreleased changelog being untracked, which is the state it sits in
        before the maintainer commits it.
        """
        changelog_dir = REPO_ROOT / "changelog"
        unreleased: list[str] = []
        released: list[str] = []
        for path in sorted(changelog_dir.glob("*.md")):
            head = path.read_text(encoding="utf-8")[:400].lower()
            (unreleased if "unreleased" in head else released).append(path.name)

        # The property the guard implements: a released changelog is never living.
        leaked = [n for n in released if f"changelog/{n}" in LIVING_DOCS]
        assert not leaked, f"released changelogs were treated as living: {leaked}"
        # And an unreleased one is living once git tracks it. It cannot be
        # asserted before that, because the guard only ever sees tracked files.
        tracked = set(_committed_markdown())
        for name in unreleased:
            if f"changelog/{name}" in tracked:
                assert f"changelog/{name}" in LIVING_DOCS
        # Between releases there is no unreleased changelog at all (the release
        # cut dates the last one), and then nothing may be living: an assertion
        # that one *must* exist would fail on the maintainer immediately after
        # every release.
        if not unreleased:
            assert not [f for f in LIVING_DOCS if f.startswith("changelog/")]

    @pytest.mark.parametrize("rel", LIVING_DOCS)
    def test_documented_paths_exist(self, rel):
        missing = _missing_paths(rel)
        assert not missing, (
            f"{rel} points at paths that do not exist:\n  "
            + "\n  ".join(missing)
            + "\nFix the doc, or move the path back — but do not leave a "
            "contributor following a dead end."
        )
