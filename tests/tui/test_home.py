"""
Tests for the Home screen.

Home is the app's map of its own capabilities, so the tests pin that the map
stays honest: every task, each with the inputs it needs, a real recent list that
prunes dead paths, and a demo-ECU action that writes a file where it says it
will. Nothing here invents numbers.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openremap.tui import paths, state
from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.home import TASKS, HomeScreen
from openremap.tui.widgets.card import TaskCard


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the workspace at a temp dir (the real one is read-only in CI)."""
    folder = tmp_path / "OpenRemap"
    folder.mkdir()
    monkeypatch.setattr(paths, "workspace", lambda: folder)
    return folder


def run(body, *, size=(100, 32)):
    """Open Home and run *body* while the app is still alive.

    Everything must be read inside the ``run_test`` context — the widgets are
    unmounted the moment it exits, and querying them afterwards raises.
    """

    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            app.push_screen(HomeScreen())
            await pilot.pause(0.1)
            return await body(pilot, app, app.screen)

    return asyncio.run(scenario())


class TestLayout:
    def test_every_task_has_a_card_with_its_number(self, workspace):
        async def body(pilot, app, screen):
            cards = list(screen.query(TaskCard))
            return [(c.number, c.title) for c in cards]

        result = run(body)
        assert result == [(number, title) for _, number, title, _, _ in TASKS]
        assert len(result) == len(TASKS) == 8

    def test_every_task_declares_inputs_and_output(self):
        for _key, _number, title, needs, produces in TASKS:
            assert needs, title
            assert produces, title
            # Card copy must fit a 3-across row at 80 columns.
            assert len(needs) + len(produces) <= 34, title

    def test_first_card_has_focus(self, workspace):
        async def body(pilot, app, screen):
            return app.focused

        focused = run(body)
        assert isinstance(focused, TaskCard)
        assert focused.number == 1


class TestActions:
    def test_every_available_card_opens_a_screen(self, workspace):
        """All eight tasks have a screen — the card must not be a broken promise.

        Previously this test asserted `True`: it was written while the screens were
        still being built, and a card that quietly did nothing was the expected
        state. Every card now opens something, so the assertion is the screen.
        """
        from openremap.tui.screens.home import TASKS, HomeScreen

        async def body(pilot, app, home):
            opened: dict[str, str] = {}
            for key, *_rest in TASKS:
                home.action_task(key)
                await pilot.pause(0.2)
                opened[key] = type(app.screen).__name__
                app.pop_screen()
                await pilot.pause(0.1)
            return opened

        opened = run(body)
        assert set(opened) == set(HomeScreen.AVAILABLE), (
            "every task the screen declares available must actually open"
        )
        for key, screen_name in opened.items():
            assert screen_name != "HomeScreen", f"{key} did not open a screen"

    def test_demo_ecu_writes_a_file_and_records_it(self, workspace):
        async def body(pilot, app, screen):
            await pilot.press("s")
            return [c.path for c in state.load().recent]

        recent = run(body)
        target = workspace / "ecus" / "demo_ecu.bin"
        assert target.is_file()
        assert target.stat().st_size > 0
        assert str(target) in recent


class TestRecents:
    def test_empty_state_says_so(self, workspace):
        async def body(pilot, app, screen):
            return (
                str(screen.query_one("#home-recent-empty").render()),
                screen.query_one("#home-recent").display,
            )

        text, visible = run(body)
        assert "nothing yet" in text
        assert "demo ECU" in text
        assert visible is False, "an empty list should not take the space"

    def test_state_prunes_missing_files(self, workspace):
        present = workspace / "a.bin"
        present.write_bytes(b"\x00")
        (workspace / ".state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "recent": [
                        {"path": str(present), "kind": "bin", "label": "a.bin", "at": ""},
                        {"path": str(workspace / "gone.bin"), "kind": "bin", "label": "gone"},
                    ],
                }
            )
        )
        loaded = state.load()
        assert [r.label for r in loaded.recent] == ["a.bin"]

    def test_recent_entries_render(self, workspace):
        present = workspace / "octavia.bin"
        present.write_bytes(b"\x00")
        (workspace / ".state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "recent": [
                        {"path": str(present), "kind": "bin", "label": "octavia.bin", "at": ""}
                    ],
                }
            )
        )

        async def body(pilot, app, screen):
            listing = screen.query_one("#home-recent")
            prompt = str(listing.get_option_at_index(0).prompt) if listing.option_count else ""
            return prompt, str(screen.query_one("#home-recent-empty").render())

        prompt, empty = run(body)
        assert "octavia.bin" in prompt
        assert "nothing yet" not in empty


class TestStatusBar:
    def test_status_shows_version_workspace_and_engine(self, workspace):
        async def body(pilot, app, screen):
            return str(screen.query_one("#home-status-left").render())

        text = run(body)
        assert "v" in text
        assert "OpenRemap" in text
        assert "engine" in text

    def test_hints_are_present(self, workspace):
        async def body(pilot, app, screen):
            return str(screen.query_one("#home-status-hints").render())

        text = run(body)
        assert "quit" in text.lower()
        # Every key the hint bar advertises must exist, and no phantom ones.
        assert "F" in text and "S" in text
        assert "F1" not in text
