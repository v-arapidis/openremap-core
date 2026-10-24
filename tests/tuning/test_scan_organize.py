"""
Tests for the batch scan's file rules.

The rules used to live in ``cli/commands/scan.py`` — inside a presentation
module — which meant a second consumer (the TUI batch screen) would have had to
copy them. These pin the rules themselves:

* which files a scan covers, and which it must *not* re-scan,
* where a classified file would be filed (computed, not done),
* and that filing copies by default, so a batch can never lose a dump.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from openremap.core.services.scan_organize import (
    apply_organize,
    plan_organize,
    scan_candidates,
    skipped_files,
)

ROOT = Path(__file__).parent.parent
M33 = ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"


def _folder(tmp_path: Path, *, copies: int = 2) -> Path:
    folder = tmp_path / "dumps"
    folder.mkdir()
    if M33.exists():
        for index in range(copies):
            shutil.copy(M33, folder / f"dump{index}.bin")
    else:  # pragma: no cover - corpus absent
        for index in range(copies):
            (folder / f"dump{index}.bin").write_bytes(bytes(4096))
    (folder / "readme.txt").write_text("not a dump", encoding="utf-8")
    (folder / "notes.hex").write_bytes(b":00000001FF")
    return folder


class TestCandidates:
    def test_covers_binaries_and_skips_the_rest(self, tmp_path):
        folder = _folder(tmp_path)
        candidates = scan_candidates(folder)
        names = [path.name for path in candidates]
        assert "dump0.bin" in names and "dump1.bin" in names
        assert "readme.txt" not in names
        assert "notes.hex" in names, ".hex is a container the engine reads"
        assert skipped_files(folder) == 1

    def test_does_not_descend_into_its_own_output(self, tmp_path):
        """A second scan must not classify the first scan's results."""
        folder = _folder(tmp_path)
        (folder / "scanned").mkdir()
        (folder / "scanned" / "already.bin").write_bytes(b"\x00" * 4096)
        names = [path.name for path in scan_candidates(folder)]
        assert "already.bin" not in names

    def test_recursive_finds_nested_dumps(self, tmp_path):
        folder = _folder(tmp_path)
        nested = folder / "sub"
        nested.mkdir()
        (nested / "deep.bin").write_bytes(b"\x00" * 4096)
        assert "deep.bin" not in [p.name for p in scan_candidates(folder)]
        assert "deep.bin" in [p.name for p in scan_candidates(folder, recursive=True)]

    def test_a_missing_folder_is_empty_not_an_error(self, tmp_path):
        assert scan_candidates(tmp_path / "nope") == []
        assert skipped_files(tmp_path / "nope") == 0


class TestPlan:
    def _row(self, path: Path, destination: str, **extra) -> dict:
        row = {"file": str(path), "destination": destination}
        row.update(extra)
        return row

    def test_identified_files_get_manufacturer_and_family(self, tmp_path):
        folder = _folder(tmp_path)
        plan = plan_organize(
            folder,
            [
                self._row(folder / "dump0.bin", "scanned", manufacturer="Bosch", ecu_family="EDC16"),
                self._row(folder / "dump1.bin", "scanned", manufacturer="Bosch", ecu_family="EDC16"),
            ],
        )
        assert plan.count == 2
        assert plan.relative_folders() == {"scanned/Bosch/EDC16/": 2}

    def test_unnamed_outcomes_stay_flat(self, tmp_path):
        folder = _folder(tmp_path)
        plan = plan_organize(
            folder,
            [
                self._row(folder / "dump0.bin", "unknown"),
                self._row(folder / "dump1.bin", "contested", manufacturer="Bosch"),
            ],
        )
        assert plan.relative_folders() == {"unknown/": 1, "contested/": 1}

    def test_planning_touches_nothing(self, tmp_path):
        folder = _folder(tmp_path)
        before = sorted(p.name for p in folder.iterdir())
        plan_organize(folder, [self._row(folder / "dump0.bin", "scanned", manufacturer="X", ecu_family="Y")])
        assert sorted(p.name for p in folder.iterdir()) == before

    def test_a_row_with_an_unknown_destination_is_ignored(self, tmp_path):
        folder = _folder(tmp_path)
        plan = plan_organize(folder, [self._row(folder / "dump0.bin", "not-a-destination")])
        assert plan.count == 0


class TestApply:
    def _plan(self, folder: Path):
        return plan_organize(
            folder,
            [
                {
                    "file": str(folder / "dump0.bin"),
                    "destination": "scanned",
                    "manufacturer": "Bosch",
                    "ecu_family": "EDC16",
                }
            ],
        )

    def test_copy_is_the_default_and_keeps_the_original(self, tmp_path):
        folder = _folder(tmp_path)
        plan = self._plan(folder)
        outcome = apply_organize(plan.files)
        assert outcome.copied == 1 and outcome.moved == 0
        assert (folder / "dump0.bin").exists(), "a copy must not lose the dump"
        assert (folder / "scanned" / "Bosch" / "EDC16" / "dump0.bin").exists()

    def test_move_moves_and_is_opt_in(self, tmp_path):
        folder = _folder(tmp_path)
        plan = self._plan(folder)
        outcome = apply_organize(plan.files, move=True)
        assert outcome.moved == 1 and outcome.copied == 0
        assert not (folder / "dump0.bin").exists()
        assert (folder / "scanned" / "Bosch" / "EDC16" / "dump0.bin").exists()

    def test_a_name_collision_renames_instead_of_overwriting(self, tmp_path):
        folder = _folder(tmp_path)
        plan = self._plan(folder)
        apply_organize(plan.files)
        second = apply_organize(plan.files)
        assert second.renamed == 1
        target = folder / "scanned" / "Bosch" / "EDC16"
        assert len(list(target.glob("dump0*.bin"))) == 2
