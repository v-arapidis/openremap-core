"""
Tests for the recent list — the workspace's memory.

What must hold: a file the operator actually used shows up on HOME, the list
survives a restart (it is written to ``.state.json`` immediately), one file is
one row no matter how many screens touched it, selecting a row opens the right
screen for that kind of file, and Shift+R clears the memory without touching the
files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openremap.tui import paths, state
from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.home import HomeScreen

ROOT = Path(__file__).parent.parent
M33 = ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    folder = tmp_path / "OpenRemap"
    folder.mkdir()
    monkeypatch.setattr(paths, "workspace", lambda: folder)
    monkeypatch.setattr(paths, "state_file", lambda: folder / ".state.json")
    monkeypatch.setattr(state.paths, "workspace", lambda: folder)
    monkeypatch.setattr(state.paths, "state_file", lambda: folder / ".state.json")
    return folder


def run(body, *, size=(110, 32)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _prompts(screen: HomeScreen) -> list[str]:
    listing = screen.query_one("#home-recent")
    return [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestRecording:
    def test_a_used_file_appears_on_home(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            return _prompts(home)

        prompts = run(body)
        assert len(prompts) == 1
        assert "0261200404-1267357689" in prompts[0]
        assert "dump" in prompts[0]

    def test_the_same_file_is_one_row(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "dump")
            app.note_recent(M33, "dump")
            app.note_recent(M33, "patched")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            return home.state.recent

        entries = run(body)
        assert len(entries) == 1, "one file, one row"
        assert entries[0].kind == "patched", "and the last use wins"

    def test_unknown_kinds_and_missing_files_are_not_recorded(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "banana")
            app.note_recent(workspace / "gone.bin", "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.2)
            return home.state.recent

        assert run(body) == []

    def test_identify_records_the_dump_it_read(self, workspace):
        """The wiring, not just the helper."""
        from openremap.tui.screens.identify import IdentifyScreen

        async def body(pilot, app):
            app.push_screen(IdentifyScreen(str(M33)))
            for _ in range(200):
                await pilot.pause(0.05)
                if state.load().recent:
                    break
            return [r.kind for r in state.load().recent]

        assert run(body) == ["dump"]


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestPersistence:
    def test_it_survives_a_restart(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "dump")
            first = _prompts(HomeScreen())  # a brand new HomeScreen = a fresh read
            return first, json.loads((workspace / ".state.json").read_text())["recent"]

        # note: HomeScreen() is built outside a running app here, which is fine —
        # the state is read in __init__ before any widget exists
        app = OpenRemapTUI()
        app.fast_boot = True

        async def scenario():
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.05)
                app.note_recent(M33, "dump")
                return HomeScreen().state.recent

        entries = asyncio.run(scenario())
        assert [r.kind for r in entries] == ["dump"]
        on_disk = json.loads((workspace / ".state.json").read_text())["recent"]
        assert on_disk and on_disk[0]["kind"] == "dump"

    def test_a_deleted_file_is_dropped_on_load(self, workspace):
        busy = workspace / "busy.bin"
        busy.write_bytes(b"\x00")
        state.record_recent(busy, "dump", "busy.bin")
        busy.unlink()
        assert state.load().recent == []


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestNavigation:
    def test_r_focuses_the_list_and_enter_opens_the_kind(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            await pilot.press("r")
            await pilot.pause(0.2)
            focused = type(app.focused).__name__
            await pilot.press("enter")
            await pilot.pause(0.5)
            return focused, type(app.screen).__name__, app.screen.path

        focused, screen_name, path = run(body)
        assert focused == "OptionList"
        assert screen_name == "IdentifyScreen"
        assert Path(path) == M33

    def test_a_recipe_row_opens_apply_with_that_recipe(self, workspace, tmp_path):
        recipe = tmp_path / "stage1.remap"
        recipe.write_text("{}", encoding="utf-8")

        async def body(pilot, app):
            app.note_recent(recipe, "recipe")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            await pilot.press("r")
            await pilot.press("enter")
            await pilot.pause(0.4)
            return type(app.screen).__name__, app.screen.recipe_path

        screen_name, chosen = run(body)
        assert screen_name == "ApplyScreen"
        assert Path(chosen) == recipe

    def test_a_folder_row_opens_batch_on_it(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33.parent, "folder")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            await pilot.press("r")
            await pilot.press("enter")
            await pilot.pause(0.4)
            return type(app.screen).__name__, app.screen.folder

        screen_name, folder = run(body)
        assert screen_name == "BatchScreen"
        assert Path(folder) == M33.parent


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestDiscoverability:
    """A key nobody can see is a key nobody has."""

    def test_the_bar_follows_the_focus(self, workspace):
        async def body(pilot, app):
            app.note_recent(M33, "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            def bar() -> str:
                return str(home.query_one("#home-status-hints").render())

            cards = bar()
            await pilot.press("r")
            await pilot.pause(0.3)
            listing = bar()
            await pilot.press("escape")
            await pilot.pause(0.3)
            return cards, listing, bar()

        cards, listing, back = run(body)
        assert "R recent" in cards
        assert "Enter open" in listing, "the list says how to open a row"
        assert "D clear list" in listing, "and which key clears it, by name"
        assert "R clear" not in listing, "R does not mean clear in this context"
        assert "Esc" in listing
        assert back == cards

    def test_the_empty_list_says_how_to_fill_it(self, workspace):
        async def body(pilot, app):
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.2)
            return str(home.query_one("#home-recent-empty").render())

        text = run(body)
        assert "identify a dump" in text or "cook a recipe" in text


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestClearing:
    @pytest.mark.parametrize("key", ["d", "delete"])
    def test_one_plain_key_clears_it_and_never_touches_the_files(self, workspace, key):
        """No shift, no second press: clearing the *list* loses nothing, because
        every file is still on disk."""

        async def body(pilot, app):
            app.note_recent(M33, "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            await pilot.press("r")           # focus the list, as the bar says
            await pilot.pause(0.2)
            before = len(_prompts(home))
            await pilot.press(key)
            await pilot.pause(0.3)
            return (
                before,
                len(home.state.recent),
                M33.exists(),
                json.loads((workspace / ".state.json").read_text())["recent"],
            )

        before, after, dump_alive, on_disk = run(body)
        assert before == 1
        assert after == 0
        assert dump_alive is True, "clearing the memory must not touch a dump"
        assert on_disk == []

    def test_r_alone_only_focuses_the_list(self, workspace):
        """The letter that focuses the list must never clear it."""

        async def body(pilot, app):
            app.note_recent(M33, "dump")
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.3)
            await pilot.press("r")
            await pilot.pause(0.2)
            await pilot.press("r")
            await pilot.pause(0.2)
            return len(home.state.recent)

        assert run(body) == 1

    def test_clearing_an_empty_list_says_so(self, workspace):
        async def body(pilot, app):
            home = HomeScreen()
            app.push_screen(home)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.2)
            return len(app._notifications)

        assert run(body) >= 1, "a key that does nothing must say why"
