"""
Tests for the HELP screen.

What must hold: the capabilities list comes from the engine's own registry (so it
cannot go stale), the family reference is the same table the CLI prints, and the
two views advertise the key that leaves each one.
"""

from __future__ import annotations

import asyncio

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.help import HelpScreen


def run(body, *, size=(120, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _rows(screen: HelpScreen) -> list[tuple[str, ...]]:
    table = screen.query_one("#help-table")
    return [
        tuple(str(cell) for cell in table.get_row_at(index))
        for index in range(table.row_count)
    ]


def _text(screen: HelpScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


class TestCapabilities:
    def test_it_lists_what_the_engine_registers(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            return _rows(screen), _text(screen, "#help-note")

        rows, note = run(body)
        assert len(rows) >= 20, "the registry has dozens of methods"
        methods = {row[1] for row in rows}
        assert {"identify", "scan", "apply_strict"} <= methods
        assert "cannot go stale" in note

    def test_every_row_names_a_kind_and_a_purpose(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            return _rows(screen)

        rows = run(body)
        kinds = {row[0] for row in rows}
        assert kinds <= {"command", "composite", "workflow"}
        assert all(row[2].strip() for row in rows), "each method explains itself"

    def test_the_key_bar_advertises_the_views(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.4)
            return _text(screen, "#help-hints")

        hints = run(body)
        assert "[F]" in hints and "families" in hints
        assert hints.count("[F]") == 1, "one key, advertised once"


class TestFamilies:
    def test_f_shows_the_coverage_every_manufacturer_declares(self):
        """The old view listed 15 Bosch entries and nothing else — it read as
        "the tool recognises 15 things" while 38 extractors declare 104 families
        across six manufacturers."""

        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            await pilot.press("f")
            await pilot.pause(0.3)
            return _rows(screen), _text(screen, "#help-note"), _text(screen, "#help-hints")

        rows, note, hints = run(body)
        manufacturers = {row[0] for row in rows}
        assert {"Bosch", "Siemens", "Denso", "Delphi", "Magneti Marelli", "Hitachi"} <= (
            manufacturers
        )
        assert "declared by" in note and "manufacturers" in note
        assert "claimed only when an extractor fires" in note
        assert "[C]" in hints

    def test_every_declared_family_is_listed_with_its_extractor(self):
        """One row per declared family, all six manufacturers, no prose filter."""

        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            await pilot.press("f")
            await pilot.pause(0.3)
            return _rows(screen), screen.declared

        rows, declared = run(body)
        assert len(rows) == len(declared) >= 100
        families = {row[1] for row in rows}
        assert {"EDC16C31", "MS43", "SH7055", "IAW 1AP", "Multec S", "SH72546"} <= families
        assert all(row[2] for row in rows), "each family names its extractor"
        assert {row[3] for row in rows} <= {"STRONG", "MODERATE", "WEAK", "?"}

    def test_the_reference_column_says_where_prose_exists(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            await pilot.press("f")
            await pilot.pause(0.3)
            return {row[1]: row[4] for row in _rows(screen)}

        by_family = run(body)
        assert by_family["EDC16C31"] == "EDC16", "a variant points at its heading"
        assert by_family["M3.3"] == "M3.x"
        assert by_family["MS43"] == "—", "Siemens is detection-only today"

    def test_highlighting_a_family_shows_what_is_written(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.5)
            await pilot.press("f")
            await pilot.pause(0.3)
            table = screen.query_one("#help-table")
            row = next(
                index
                for index in range(table.row_count)
                if str(table.get_row_at(index)[1]) == "EDC15C2"
            )
            table.move_cursor(row=row)
            await pilot.pause(0.3)
            documented = _text(screen, "#help-note")
            row = next(
                index
                for index in range(table.row_count)
                if str(table.get_row_at(index)[1]) == "MS43"
            )
            table.move_cursor(row=row)
            await pilot.pause(0.3)
            return documented, _text(screen, "#help-note")

        documented, undocumented = run(body)
        assert "described under EDC15" in documented
        assert "512 KB" in documented
        assert "no written reference yet" in undocumented
        # the counts stay above the card in both cases
        assert "families declared by" in documented

    def test_c_goes_back_to_capabilities(self):
        async def body(pilot, app):
            screen = HelpScreen()
            app.push_screen(screen)
            await pilot.pause(0.4)
            await pilot.press("f")
            await pilot.pause(0.2)
            await pilot.press("c")
            await pilot.pause(0.2)
            return screen.view

        assert run(body) == "capabilities"


class TestReachability:
    def test_h_opens_help_without_a_modifier(self):
        """`?` is Shift+/ on most layouts, so a plain letter is the way in."""
        from openremap.tui.screens.home import HomeScreen

        async def body(pilot, app):
            app.push_screen(HomeScreen())
            await pilot.pause(0.2)
            await pilot.press("h")
            await pilot.pause(0.4)
            return type(app.screen).__name__

        assert run(body) == "HelpScreen"

    def test_home_opens_help_with_the_question_mark(self):
        from openremap.tui.screens.home import HomeScreen

        async def body(pilot, app):
            app.push_screen(HomeScreen())
            await pilot.pause(0.2)
            await pilot.press("question_mark")
            await pilot.pause(0.4)
            return type(app.screen).__name__

        assert run(body) == "HelpScreen"

    def test_home_advertises_the_key(self):
        from openremap.tui.screens.home import HomeScreen

        async def body(pilot, app):
            app.push_screen(HomeScreen())
            await pilot.pause(0.2)
            return str(app.screen.query_one("#home-status-hints").render())

        assert "H help" in run(body)
