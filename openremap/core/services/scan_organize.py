"""
Which files a folder scan covers, and what "organize" would do to them.

Both halves used to live in ``cli/commands/scan.py`` — file traversal, the
destination policy and the actual moves inside a presentation module. That was a
layering violation waiting for its second consumer: the TUI batch screen needs
exactly the same rules, and copying them would have produced two scanners that
drift apart.

So the rules live here:

* :func:`scan_candidates` — which files a scan covers (extensions, recursion,
  and never descending into the tool's own output folders).
* :func:`plan_organize` — where each classified file *would* go, computed
  without touching the filesystem.
* :func:`apply_organize` — carry out a plan, copying by default and moving only
  when asked.

Nothing here identifies anything: classification is
``core.manufacturers.scan.classify_file``, one file at a time.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openremap.core.manufacturers.scan import (
    ALL_DEST,
    ORGANIZABLE_DEST,
    VALID_EXTENSIONS,
)

__all__ = [
    "OrganizeOutcome",
    "OrganizePlan",
    "PlannedFile",
    "apply_organize",
    "plan_organize",
    "scan_candidates",
]

#: Folder names a scan never descends into: its own results.
_OUTPUT_FOLDERS = frozenset(ALL_DEST)


def scan_candidates(root: Path, *, recursive: bool = False) -> list[Path]:
    """Every file under *root* that a scan should classify, sorted.

    Skips anything that is not a binary the engine can read, and skips the
    output folders this tool creates (``scanned/``, ``unknown/`` …) so a second
    scan of the same folder does not classify its own results.
    """
    if not root.is_dir():
        return []
    walker = root.rglob("*") if recursive else root.iterdir()
    try:
        candidates = [
            path
            for path in walker
            if path.is_file()
            and path.suffix.lower() in VALID_EXTENSIONS
            and path.name not in _OUTPUT_FOLDERS
            and not any(part in _OUTPUT_FOLDERS for part in path.relative_to(root).parts[:-1])
        ]
    except OSError:
        return []
    return sorted(candidates)


def skipped_files(root: Path, *, recursive: bool = False) -> int:
    """How many files a scan would leave alone — counted, never guessed."""
    if not root.is_dir():
        return 0
    walker = root.rglob("*") if recursive else root.iterdir()
    try:
        return sum(
            1
            for path in walker
            if path.is_file() and path.suffix.lower() not in VALID_EXTENSIONS
        )
    except OSError:
        return 0


@dataclass(frozen=True)
class PlannedFile:
    """One file and the folder it would end up in."""

    source: Path
    destination: Path


@dataclass
class OrganizePlan:
    """What ``organize`` would do — computed, not done."""

    root: Path
    files: list[PlannedFile] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.files)

    def by_folder(self) -> dict[Path, int]:
        """Destination folder → how many files would land in it."""
        counts: dict[Path, int] = {}
        for item in self.files:
            counts[item.destination] = counts.get(item.destination, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))

    def relative_folders(self) -> dict[str, int]:
        """The same counts, as paths relative to the scanned root (for display)."""
        return {
            str(folder.relative_to(self.root)) + "/": count
            for folder, count in self.by_folder().items()
        }


def _nested_folder(base: Path, row: Mapping[str, Any]) -> Path:
    """``scanned/Bosch/EDC17`` for identified files, the flat folder otherwise.

    Only ``scanned`` and ``sw_missing`` get manufacturer/family nesting: those
    are the outcomes where exactly one extractor spoke, so there is a defensible
    name to file the dump under. A contested file has two names and an unknown
    file has none, so they stay flat.
    """
    destination = str(row.get("destination") or "")
    if destination not in ORGANIZABLE_DEST:
        return base
    manufacturer = str(row.get("manufacturer") or "unknown").strip() or "unknown"
    family = str(row.get("ecu_family") or "unknown").strip() or "unknown"
    safe = [part.replace("/", "_").replace("\\", "_") for part in (manufacturer, family)]
    return base / safe[0] / safe[1]


def plan_organize(
    root: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    nested: bool = True,
) -> OrganizePlan:
    """Where each classified file would move to.

    *rows* are the scan results (each carrying ``file`` and ``destination``, plus
    the extracted identity when there is one). Nothing is created or moved here.
    """
    plan = OrganizePlan(root=root)
    for row in rows:
        source = Path(str(row.get("file") or ""))
        destination_name = str(row.get("destination") or "")
        if not source.name or destination_name not in ALL_DEST:
            continue
        base = root / destination_name
        folder = _nested_folder(base, row) if nested else base
        plan.files.append(PlannedFile(source=source, destination=folder))
    return plan


# -- carrying it out -------------------------------------------------------
@dataclass
class OrganizeOutcome:
    """What ``apply_organize`` actually did."""

    copied: int = 0
    moved: int = 0
    renamed: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    folders: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.copied + self.moved

    def as_dict(self) -> dict[str, Any]:
        return {
            "copied": self.copied,
            "moved": self.moved,
            "renamed": self.renamed,
            "failed": list(self.failed),
            "folders": dict(self.folders),
            "total": self.total,
        }


def _free_target(folder: Path, name: str) -> tuple[Path, bool]:
    """A path in *folder* that does not collide, and whether it was renamed."""
    target = folder / name
    if not target.exists():
        return target, False
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while target.exists():
        target = folder / f"{stem}__{counter}{suffix}"
        counter += 1
    return target, True


def apply_organize(
    plan: Sequence[PlannedFile],
    *,
    move: bool = False,
) -> OrganizeOutcome:
    """Carry out *plan*: copy (default) or move each file into place.

    Copy is the default because it cannot lose a dump: the originals stay where
    they were and the organised tree is an addition. Moving is available for
    when the operator really wants one tree, and is never the quiet default.
    """
    outcome = OrganizeOutcome()
    for item in plan:
        try:
            item.destination.mkdir(parents=True, exist_ok=True)
            target, renamed = _free_target(item.destination, item.source.name)
            if move:
                shutil.move(str(item.source), str(target))
                outcome.moved += 1
            else:
                shutil.copy2(item.source, target)
                outcome.copied += 1
            if renamed:
                outcome.renamed += 1
            key = str(item.destination)
            outcome.folders[key] = outcome.folders.get(key, 0) + 1
        except OSError as exc:
            outcome.failed.append({"file": str(item.source), "error": str(exc)})
    return outcome
