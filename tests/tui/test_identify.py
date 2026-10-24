"""
Tests for the IDENTIFY screen.

The screen's job is to be *right*, not flattering, so the tests pin both halves:

* a recognised dump shows family, software numbers and the detection strength,
* an unrecognised file says UNKNOWN and offers honest next steps,
* "full analyse" adds the heavy passes (health, checksums, layout, maps),
* nothing is read from disk until a file is actually chosen.

Real dumps live in the gitignored corpus, so those tests skip without it; the
unknown-file test uses ``generate_dump`` — synthetic data, no ROM involved.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from textual.screen import Screen

from openremap.api import call
from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.identify import IdentifyScreen
from openremap.tui.screens.picker import FilePicker

ROOT = Path(__file__).parent.parent.parent
EDC16 = ROOT / "tests/data/ECUs/Bosch/EDC16/016FEorg.127__1__1.bin"


def run(body, *, size=(110, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


async def _wait(pilot, predicate, *, rounds: int = 600):
    for _ in range(rounds):
        if predicate():
            return True
        await pilot.pause(0.02)
    return False


def _text(screen, selector: str) -> str:
    return str(screen.query_one(selector).render())


class TestEmptyState:
    def test_waits_for_a_file(self):
        async def body(pilot, app):
            app.push_screen(IdentifyScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#identify-body")

        assert "Press F" in run(body)


@pytest.mark.skipif(not EDC16.exists(), reason="test corpus absent")
class TestKnownDump:
    def test_shows_family_software_and_strength(self):
        async def body(pilot, app):
            screen = IdentifyScreen(str(EDC16))
            app.push_screen(screen)
            await _wait(pilot, lambda: screen.identity is not None)
            return (
                _text(screen, "#identify-headline"),
                _text(screen, "#identify-body"),
            )

        headline, body = run(body)
        assert "EDC16" in headline
        assert "STRONG" in headline
        assert "1037372733" in body  # software version
        assert "03G906016FE" in body  # OEM part number

    def test_maps_opens_the_maps_screen_with_this_file(self):
        async def body(pilot, app):
            screen = IdentifyScreen(str(EDC16))
            app.push_screen(screen)
            await _wait(pilot, lambda: screen.identity is not None)
            await pilot.press("m")
            await pilot.pause(0.1)
            pushed = app.screen
            return type(pushed).__name__, Path(pushed.path).name if pushed.path else None

        name, file_name = run(body)
        assert name == "MapsScreen"
        assert file_name == EDC16.name

    def test_inspect_opens_the_inspect_screen(self):
        async def body(pilot, app):
            screen = IdentifyScreen(str(EDC16))
            app.push_screen(screen)
            await _wait(pilot, lambda: screen.identity is not None)
            await pilot.press("i")
            await pilot.pause(0.1)
            return type(app.screen).__name__

        assert run(body) == "InspectScreen"

    def test_there_is_no_second_analysis_key(self):
        """One screen, one key: 'a' must not silently duplicate 'i'."""
        keys = {binding.key for binding in IdentifyScreen.BINDINGS}
        assert "i" in keys
        assert "a" not in keys

    def test_only_real_keys_are_advertised(self):
        """Every hint must map to a binding — no phantom keys on the bar."""
        keys = {binding.key for binding in IdentifyScreen.BINDINGS}
        assert keys == {"f", "i", "m", "c", "escape"}


class TestUnknownDump:
    def test_says_unknown_and_offers_a_next_step(self, tmp_path):
        dump = call("generate_dump", {"size": 65536, "seed": 3})
        target = tmp_path / "synthetic.bin"
        target.write_bytes(base64.b64decode(dump["data_b64"]))

        async def body(pilot, app):
            screen = IdentifyScreen(str(target))
            app.push_screen(screen)
            await _wait(pilot, lambda: screen.identity is not None)
            return (
                _text(screen, "#identify-headline"),
                _text(screen, "#identify-next"),
            )

        headline, next_line = run(body)
        assert "UNKNOWN" in headline
        assert "no extractor" in headline
        # Only keys that exist are advertised: inspect, and trying another file.
        assert "[I]" in next_line
        assert "[F]" in next_line
        assert "[W]" not in next_line  # contribute has no screen yet
        assert "[M]" in next_line  # maps works on unclaimed dumps too


class TestFilePicker:
    def test_escape_cancels_with_none(self, tmp_path):
        seen: list[object] = []

        async def body(pilot, app):
            app.push_screen(FilePicker(tmp_path), seen.append)
            await pilot.pause(0.05)
            await pilot.press("escape")
            await pilot.pause(0.05)

        run(body)
        assert seen == [None]

    def test_choosing_a_binary_returns_its_path(self, tmp_path):
        target = tmp_path / "dump.bin"
        target.write_bytes(b"\x00")
        seen: list[object] = []

        async def body(pilot, app):
            app.push_screen(FilePicker(tmp_path), seen.append)
            await pilot.pause(0.05)
            app.screen._choose(target)
            await pilot.pause(0.05)

        run(body)
        assert seen and Path(seen[0]).name == "dump.bin"

    def test_a_recipe_is_accepted_when_the_picker_asks_for_recipes(self, tmp_path):
        """The list that filters the tree is the list that accepts the answer.

        This was a real bug: APPLY opened the picker for ``.remap`` files, the
        tree showed them, and Enter silently did nothing because the acceptance
        check still tested ``BINARY_SUFFIXES``.
        """
        from openremap.tui.screens.picker import RECIPE_SUFFIXES

        recipe = tmp_path / "stage1.remap"
        recipe.write_text("{}", encoding="utf-8")
        binary = tmp_path / "dump.bin"
        binary.write_bytes(b"\x00")
        seen: list[object] = []

        async def body(pilot, app):
            picker = FilePicker(tmp_path, suffixes=RECIPE_SUFFIXES)
            app.push_screen(picker, seen.append)
            await pilot.pause(0.1)
            accepts = (picker.accepts(recipe), picker.accepts(binary))
            picker._choose(recipe)
            await pilot.pause(0.05)
            return accepts

        accepts = run(body)
        assert accepts == (True, False)
        assert seen and Path(seen[0]).name == "stage1.remap"

    def test_the_tree_only_shows_the_suffixes_it_accepts(self, tmp_path):
        from openremap.tui.screens.picker import RECIPE_SUFFIXES

        (tmp_path / "stage1.remap").write_text("{}", encoding="utf-8")
        (tmp_path / "dump.bin").write_bytes(b"\x00")

        async def body(pilot, app):
            picker = FilePicker(tmp_path, suffixes=RECIPE_SUFFIXES)
            app.push_screen(picker)
            await pilot.pause(0.1)
            tree = picker.query_one("_BinaryTree")
            # filter_paths is what fills the tree — assert on the decision itself
            return [path.name for path in tree.filter_paths(sorted(tmp_path.iterdir()))]

        listed = run(body)
        assert listed == ["stage1.remap"]

    def test_a_wrong_suffix_is_refused_out_loud(self, tmp_path):
        from openremap.tui.screens.picker import RECIPE_SUFFIXES

        binary = tmp_path / "dump.bin"
        binary.write_bytes(b"\x00")
        seen: list[object] = []

        async def body(pilot, app):
            picker = FilePicker(tmp_path, suffixes=RECIPE_SUFFIXES)
            app.push_screen(picker, seen.append)
            await pilot.pause(0.1)
            picker._choose(binary)
            await pilot.pause(0.05)
            return len(app._notifications)

        assert run(body) >= 1, "refusing a file must say so"
        assert seen == [], "and must not return it"

    def test_left_goes_up_a_level(self, tmp_path):
        child = tmp_path / "dumps"
        child.mkdir()

        async def body(pilot, app):
            app.push_screen(FilePicker(child))
            await pilot.pause(0.1)
            await pilot.press("left")
            await pilot.pause(0.1)
            return app.screen.query_one("_BinaryTree").path

        assert run(body) == tmp_path.resolve()

    def test_choosing_remembers_the_directory(self, tmp_path, monkeypatch):
        from openremap.tui import paths, state

        workspace = tmp_path / "OpenRemap"
        workspace.mkdir()
        monkeypatch.setattr(paths, "workspace", lambda: workspace)
        target = tmp_path / "dump.bin"
        target.write_bytes(b"\x00")

        async def body(pilot, app):
            app.push_screen(FilePicker(tmp_path))
            await pilot.pause(0.1)
            app.screen._choose(target)
            await pilot.pause(0.1)

        run(body)
        assert state.load().last_dirs.get("open") == str(tmp_path)

    def test_only_binary_files_are_offered(self, tmp_path):
        (tmp_path / "dump.bin").write_bytes(b"\x00")
        (tmp_path / "notes.txt").write_text("not a dump")

        async def body(pilot, app):
            app.push_screen(FilePicker(tmp_path))
            await pilot.pause(0.1)
            tree = app.screen.query_one("_BinaryTree")
            return [c.data.path.name for c in tree.root.children]

        names = run(body)
        assert "dump.bin" in names
        assert "notes.txt" not in names


class TestActivityIndicator:
    """A long action must say what it is doing and for how long."""

    def test_bar_shows_a_spinner_and_elapsed_time(self):
        from openremap.tui.widgets.activity import ActivityBar

        async def body(pilot, app):
            bar = ActivityBar(delay=0)
            app.push_screen(_BarHost(bar))
            await pilot.pause(0.05)
            bar.start("identifying")
            await pilot.pause(0.25)
            return bar, str(bar.render())

        bar, text = run(body)
        assert bar.running is True
        assert "identifying" in text
        assert "s" in text  # the elapsed-seconds counter
        assert any(frame in text for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def test_bar_stops_and_keeps_the_timing(self):
        from openremap.tui.widgets.activity import ActivityBar

        async def body(pilot, app):
            bar = ActivityBar(delay=0)
            app.push_screen(_BarHost(bar))
            await pilot.pause(0.05)
            bar.start("identifying")
            await pilot.pause(0.1)
            bar.stop("identified in 42 ms")
            await pilot.pause(0.15)
            return bar, str(bar.render())

        bar, text = run(body)
        assert bar.running is False
        assert "identified in 42 ms" in text

    def test_fast_work_does_not_flash_a_spinner(self):
        from openremap.tui.widgets.activity import ActivityBar

        async def body(pilot, app):
            bar = ActivityBar(delay=0.3)
            app.push_screen(_BarHost(bar))
            await pilot.pause(0.05)
            bar.start("identifying")
            bar.stop("identified in 5 ms")  # finishes before the delay elapses
            await pilot.pause(0.4)
            return bar, str(bar.render())

        bar, text = run(body)
        assert bar.running is False
        assert "identified in 5 ms" in text


class _BarHost(Screen):
    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self):
        yield self._widget
