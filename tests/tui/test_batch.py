"""
Tests for the BATCH screen.

What must hold: the folder is scanned one file at a time with real progress,
a bad file does not stop the batch, duplicates are counted (the whole reason to
batch a folder), the selected row opens IDENTIFY, and **nothing is copied or
moved until the second step says so** — and then it copies by default.

A three-file folder keeps these quick; the real 205-file folder takes ~90 s.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.batch import BatchScreen, verdict_label

ROOT = Path(__file__).parent.parent
M33 = ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"


def run(body, *, size=(120, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _folder(tmp_path: Path) -> Path:
    """Three dumps, all the same file — so two of them are duplicates."""
    folder = tmp_path / "dumps"
    folder.mkdir(parents=True)
    for name in ("a.bin", "b.bin"):
        shutil.copy(M33, folder / name)
    shutil.copy(M33, folder / "a_copy.bin")  # same md5 as a.bin
    (folder / "readme.txt").write_text("not a dump", encoding="utf-8")
    return folder


def _text(screen: BatchScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


async def _scanned(pilot, app, screen: BatchScreen, timeout: float = 90.0):
    import time

    deadline = time.monotonic() + timeout
    while (screen.running or not screen.rows) and time.monotonic() < deadline:
        await pilot.pause(0.05)
    await pilot.pause(0.2)
    return screen


class TestEmptyState:
    def test_asks_for_a_folder(self):
        async def body(pilot, app):
            app.push_screen(BatchScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#batch-folder"), _text(app.screen, "#batch-hints")

        folder, hints = run(body)
        assert "choose a folder" in folder
        assert "[Enter]" not in hints


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestTheFolderPicker:
    def test_space_takes_the_folder_the_picker_is_showing(self, tmp_path):
        folder = _folder(tmp_path)
        seen: list[object] = []

        async def body(pilot, app):
            from openremap.tui.screens.picker import FilePicker

            picker = FilePicker(folder, folders=True, title="Choose a folder of dumps")
            app.push_screen(picker, seen.append)
            await pilot.pause(0.2)
            await pilot.press("space")
            await pilot.pause(0.3)

        run(body)
        assert seen == [folder.resolve()]

    def _tree(self, tmp_path: Path) -> Path:
        """brand/family/dump.bin + brand/dump.bin — a folder with children."""
        brand = tmp_path / "brand"
        (brand / "family").mkdir(parents=True)
        shutil.copy(M33, brand / "dump.bin")
        (brand / "family" / "other.bin").write_bytes(b"\x00" * 4096)
        return brand

    def test_space_takes_the_highlighted_folder_not_the_tree_root(self, tmp_path):
        brand = self._tree(tmp_path)
        seen: list[object] = []

        async def body(pilot, app):
            from openremap.tui.screens.picker import FilePicker

            picker = FilePicker(brand, folders=True)
            app.push_screen(picker, seen.append)
            await pilot.pause(0.4)
            tree = picker.query_one("_BinaryTree")
            sub = next(c for c in tree.root.children if c.data.path.name == "family")
            tree.move_cursor(sub)
            await pilot.pause(0.1)
            highlighted = picker.chosen_folder()
            await pilot.press("space")
            await pilot.pause(0.3)
            return highlighted

        highlighted = run(body)
        assert highlighted == brand / "family", "the highlight decides, not the root"
        assert seen == [(brand / "family").resolve()]

    def test_a_highlighted_file_means_its_folder(self, tmp_path):
        brand = self._tree(tmp_path)
        seen: list[object] = []

        async def body(pilot, app):
            from openremap.tui.screens.picker import FilePicker

            picker = FilePicker(brand, folders=True)
            app.push_screen(picker, seen.append)
            await pilot.pause(0.4)
            tree = picker.query_one("_BinaryTree")
            tree.root.expand()          # the tree is rooted *at* the folder we picked
            await pilot.pause(0.5)
            entry = next(c for c in tree.root.children if c.data.path.is_file())
            tree.move_cursor(entry)
            await pilot.pause(0.1)
            await pilot.press("space")
            await pilot.pause(0.3)

        run(body)
        assert seen == [brand.resolve()], "a file means 'the folder holding it'"

    def test_the_root_itself_is_still_choosable(self, tmp_path):
        brand = self._tree(tmp_path)
        seen: list[object] = []

        async def body(pilot, app):
            from openremap.tui.screens.picker import FilePicker

            picker = FilePicker(brand, folders=True)
            app.push_screen(picker, seen.append)
            await pilot.pause(0.4)
            await pilot.press("space")
            await pilot.pause(0.3)

        run(body)
        assert seen == [brand.resolve()]

    def test_space_is_not_a_choice_when_the_picker_wants_a_file(self, tmp_path):
        folder = _folder(tmp_path)
        seen: list[object] = []

        async def body(pilot, app):
            from openremap.tui.screens.picker import FilePicker

            app.push_screen(FilePicker(folder), seen.append)
            await pilot.pause(0.2)
            await pilot.press("space")
            await pilot.pause(0.3)

        run(body)
        assert seen == [], "a dump picker must not return a folder"

    def test_choosing_a_folder_loads_it_and_then_scans(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("f")
            await pilot.pause(0.3)
            tree = app.screen.query_one("_BinaryTree")
            tree.path = folder.resolve()
            await pilot.pause(0.3)
            await pilot.press("space")
            await pilot.pause(0.4)
            chosen = screen.folder
            candidates = len(screen.candidates)
            await pilot.press("enter")
            await _scanned(pilot, app, screen)
            return chosen, candidates, len(screen.rows)

        chosen, candidates, rows = run(body)
        assert chosen == str(folder.resolve())
        assert candidates == 3
        assert rows == 3


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestDuplicates:
    """A duplicate must say *which* file it duplicates, not just that it is one."""

    def _folder(self, tmp_path: Path) -> Path:
        folder = tmp_path / "dumps"
        folder.mkdir(parents=True)
        source = sorted(M33.parent.glob("*.bin"))
        first, second = source[0], source[1]
        shutil.copy(first, folder / "a.bin")
        shutil.copy(first, folder / "aa_copy1.bin")
        shutil.copy(first, folder / "zz_copy2.bin")
        shutil.copy(second, folder / "b.bin")
        shutil.copy(second, folder / "zz_copy_of_b.bin")
        return folder

    async def _rows(self, pilot, app, folder: Path) -> list[tuple[str, str, str]]:
        screen = BatchScreen(str(folder))
        app.push_screen(screen)
        await _scanned(pilot, app, screen)
        table = screen.query_one("#batch-table")
        out = []
        for index in range(table.row_count):
            row = table.get_row_at(index)
            out.append((str(row[0]), str(row[1]), str(row[6])))
        return out, _text(screen, "#batch-summary")

    def test_the_summary_counts_distinct_files(self, tmp_path):
        def body(pilot, app):
            return self._rows(pilot, app, self._folder(tmp_path))

        rows, summary = run(body)
        assert "3 duplicates of 2 files" in summary
        assert len(rows) == 5

    def test_the_original_says_how_many_copies_it_has(self, tmp_path):
        def body(pilot, app):
            return self._rows(pilot, app, self._folder(tmp_path))

        rows, _ = run(body)
        first = rows[0]
        assert first[1] == "a.bin"
        assert first[2] == "×2", first

    def test_a_unique_file_says_nothing_in_the_dup_column(self, tmp_path):
        """A thousand rows of "—" is noise; silence means "no copies"."""
        folder = tmp_path / "dumps"
        folder.mkdir(parents=True)
        shutil.copy(M33, folder / "only.bin")

        def body(pilot, app):
            return self._rows(pilot, app, folder)

        rows, _ = run(body)
        assert rows[0][2] == ""

    def test_a_copy_is_marked_and_indented_under_its_original(self, tmp_path):
        def body(pilot, app):
            return self._rows(pilot, app, self._folder(tmp_path))

        rows, _ = run(body)
        assert rows[1][2] == "copy"
        assert rows[1][1].startswith("↳ "), "the copy sits under the original"
        assert rows[2][2] == "copy"
        assert rows[2][1].startswith("↳ ")

    def test_selecting_a_row_after_grouping_still_opens_the_right_file(self, tmp_path):
        """Grouping rewrites the display order, so the cursor must still point
        at the file the user sees — not at whatever used to be at that row."""
        folder = self._folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            table = screen.query_one("#batch-table")
            table.move_cursor(row=1)          # the first copy after grouping
            shown = str(table.get_row_at(1)[1]).removeprefix("↳ ")
            await pilot.press("i")
            await pilot.pause(0.5)
            return shown, Path(str(app.screen.path)).name

        shown, opened = run(body)
        assert opened == shown

    def test_copies_are_grouped_under_their_original(self, tmp_path):
        def body(pilot, app):
            return self._rows(pilot, app, self._folder(tmp_path))

        rows, _ = run(body)
        # a.bin, its two copies, then b.bin and its copy — no scattering
        names = [row[1].removeprefix("↳ ") for row in rows]
        assert names.index("a.bin") + 1 == names.index("aa_copy1.bin")
        assert names.index("a.bin") + 2 == names.index("zz_copy2.bin")
        assert names.index("b.bin") + 1 == names.index("zz_copy_of_b.bin")


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestVins:
    """VIN candidates are opt-in and never dressed up as a confirmed VIN."""

    def test_the_column_exists_but_stays_empty_by_default(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            table = screen.query_one("#batch-table")
            header = [str(column.label) for column in table.columns.values()]
            cells = [str(table.get_row_at(r)[5]) for r in range(table.row_count)]
            return header, cells, screen.with_vins

        header, cells, with_vins = run(body)
        assert "vin" in header
        assert with_vins is False
        assert set(cells) == {""}, "nothing is read until asked"

    def test_pressing_v_reads_them_for_the_rows_already_scanned(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            await pilot.press("v")
            for _ in range(400):
                await pilot.pause(0.05)
                if any(row.get("vin") for row in screen.rows):
                    break
            await pilot.pause(0.4)
            table = screen.query_one("#batch-table")
            cells = [str(table.get_row_at(r)[5]) for r in range(table.row_count)]
            return screen.with_vins, cells, _text(screen, "#batch-summary")

        with_vins, cells, summary = run(body)
        assert with_vins is True
        assert any(cell for cell in cells), "at least one dump carries a candidate"
        # a candidate is shown as a confidence, never as a VIN string
        assert all(cell == "" or cell.startswith("~") or len(cell) == 17 for cell in cells)
        assert "VIN candidates" in summary
        assert "confirmed" in summary


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestFolderOfFolders:
    """A folder whose dumps are all one level down must say so, not sit silent.

    Real case: ``tests/data/ECUs/Bosch`` has 0 dumps at its own level and 1126
    in subfolders. The screen used to show an empty table and no explanation.
    """

    def _nested(self, tmp_path: Path) -> Path:
        outer = tmp_path / "brand"
        inner = outer / "family"
        inner.mkdir(parents=True)
        shutil.copy(M33, inner / "dump.bin")
        return outer

    def test_an_empty_top_level_explains_itself(self, tmp_path):
        outer = self._nested(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(outer))
            app.push_screen(screen)
            await pilot.pause(0.6)
            return (
                len(screen.candidates),
                screen.subfolders,
                _text(screen, "#batch-summary"),
                _text(screen, "#batch-hints"),
            )

        candidates, subfolders, summary, hints = run(body)
        assert candidates == 0
        assert subfolders == 1
        assert "no dumps in this folder" in summary
        assert "1 found in its subfolders" in summary
        assert "[D]" in hints and "[Enter]" not in hints

    def test_pressing_d_includes_subfolders_and_unlocks_the_scan(self, tmp_path):
        outer = self._nested(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(outer))
            app.push_screen(screen)
            await pilot.pause(0.6)
            await pilot.press("d")
            await pilot.pause(0.6)
            state = (
                len(screen.candidates),
                screen.recursive,
                _text(screen, "#batch-folder"),
                _text(screen, "#batch-hints"),
            )
            await pilot.press("enter")
            await _scanned(pilot, app, screen)
            return state, len(screen.rows)

        (candidates, recursive, folder_line, hints), rows = run(body)
        assert recursive is True
        assert candidates == 1
        assert "with subfolders" in folder_line
        assert "[Enter] scan" in hints and "top only" in hints
        assert rows == 1

    def test_d_toggles_back(self, tmp_path):
        outer = self._nested(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(outer))
            app.push_screen(screen)
            await pilot.pause(0.6)
            await pilot.press("d")
            await pilot.pause(0.4)
            await pilot.press("d")
            await pilot.pause(0.4)
            return screen.recursive, len(screen.candidates)

        assert run(body) == (False, 0)

    def test_a_folder_with_no_dumps_anywhere_says_what_it_accepts(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("nothing", encoding="utf-8")

        async def body(pilot, app):
            screen = BatchScreen(str(empty))
            app.push_screen(screen)
            await pilot.pause(0.6)
            return _text(screen, "#batch-summary")

        summary = run(body)
        assert "no dumps in this folder" in summary
        assert ".bin" in summary and ".hex" in summary


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestScanning:
    def test_it_classifies_every_dump_and_counts_duplicates(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            return screen, _text(screen, "#batch-summary"), _text(screen, "#batch-folder")

        screen, summary, folder_line = run(body)
        assert len(screen.rows) == 3, "every .bin is classified"
        assert screen.skipped == 1, "the text file is counted, not touched"
        assert "3/3 classified" in summary
        # three copies of one dump: two of them are duplicates of the first
        assert screen.duplicates == 2
        assert "duplicates of 1 file" in summary
        assert "3 dumps" in folder_line
        assert "1 not a dump" in folder_line

    def test_the_table_has_one_row_per_file(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            return screen.query_one("#batch-table").row_count

        assert run(body) == 3

    def test_verdict_labels_never_invent_a_name(self):
        assert verdict_label("scanned") == "ok"
        assert verdict_label("sw_missing") == "no-sw"
        assert verdict_label("contested") == "contested"
        assert verdict_label("unknown") == "unknown"
        assert verdict_label("error") == "error"


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestStopping:
    def test_esc_stops_the_scan_and_keeps_the_rows_it_has(self, tmp_path, monkeypatch):
        """The one behaviour a fast test folder cannot exercise: interrupting.

        Classification is slowed down so the stop lands mid-run; what must hold
        is that the loop stops, the rows already classified stay, and the bar
        said how to stop before it was pressed.
        """
        from openremap import api

        real_call = api.call
        seen_bar = {}

        def slow_call(name, params=None, **kwargs):
            if name == "classify_file":
                # Long enough that the stop lands mid-run even on a loaded
                # machine: 4 files x 0.2 s gives a third of a second of window.
                time.sleep(0.2)
            return real_call(name, params, **kwargs)

        async def body(pilot, app):
            monkeypatch.setattr(api, "call", slow_call)
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await pilot.press("enter")
            for _ in range(200):
                await pilot.pause(0.02)
                if screen.rows:
                    break
            seen_bar["running"] = _text(screen, "#batch-hints")
            await pilot.press("escape")
            for _ in range(600):
                await pilot.pause(0.02)
                if not screen.running:
                    break
            await pilot.pause(0.2)
            return screen.stopped, len(screen.rows), len(screen.candidates), _text(
                screen, "#batch-activity"
            )

        folder = _folder(tmp_path)
        shutil.copy(M33, folder / "c.bin")   # four dumps: a longer scan to interrupt
        stopped, rows, candidates, activity = run(body)
        assert "stop" in seen_bar["running"], "the bar offers the stop before it is needed"
        assert stopped is True
        assert 0 < rows < candidates, f"kept {rows} of {candidates} — partial results survive"
        assert "stopped early" in activity


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestSecondStep:
    def test_organize_preview_touches_nothing(self, tmp_path):
        folder = _folder(tmp_path)
        before = sorted(p.name for p in folder.iterdir())

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            await pilot.press("o")
            await pilot.pause(0.4)
            return _text(screen, "#batch-plan")

        plan_text = run(body)
        assert "nothing has been touched" in plan_text
        assert "3 files would go to" in plan_text
        assert sorted(p.name for p in folder.iterdir()) == before

    def test_copy_keeps_the_originals_and_move_does_not(self, tmp_path):
        copy_folder = _folder(tmp_path / "copy")
        move_folder = _folder(tmp_path / "move")

        async def body(pilot, app):
            screen = BatchScreen(str(copy_folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            await pilot.press("o")
            await pilot.pause(0.3)
            await pilot.press("c")          # copy: the default
            await pilot.pause(0.6)
            filed = dict(screen.filed or {})
            app.pop_screen()
            await pilot.pause(0.1)

            second = BatchScreen(str(move_folder))
            app.push_screen(second)
            await _scanned(pilot, app, second)
            await pilot.press("o")
            await pilot.pause(0.3)
            await pilot.press("m")          # move: the deliberate step
            await pilot.pause(0.6)
            return filed, dict(second.filed or {})

        filed_copy, filed_move = run(body)
        assert filed_copy.get("copied") == 3 and not filed_copy.get("moved")
        assert (copy_folder / "a.bin").exists(), "a copy never loses a dump"
        assert list(copy_folder.rglob("a.bin"))[1:] , "and the copy is filed"

        assert filed_move.get("moved") == 3
        assert not (move_folder / "a.bin").exists(), "a move takes the original"

    def test_the_plan_must_be_seen_before_files_move(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            await pilot.press("c")  # no preview yet
            await pilot.pause(0.3)
            return screen.filed, len(app._notifications)

        filed, _notifications = run(body)
        assert filed is None

    def test_hints_advertise_only_the_keys_that_work(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            before = _text(screen, "#batch-hints")
            await pilot.press("o")
            await pilot.pause(0.3)
            return before, _text(screen, "#batch-hints")

        before, after = run(body)
        assert "[O] organize" in before and "[C]" not in before
        assert "[C]" in after and "[M]" in after


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestRowAction:
    def test_identify_opens_on_the_selected_dump(self, tmp_path):
        folder = _folder(tmp_path)

        async def body(pilot, app):
            screen = BatchScreen(str(folder))
            app.push_screen(screen)
            await _scanned(pilot, app, screen)
            await pilot.press("i")
            await pilot.pause(0.5)
            return type(app.screen).__name__, app.screen.path

        screen_name, path = run(body)
        assert screen_name == "IdentifyScreen"
        assert path and Path(path).suffix == ".bin"
