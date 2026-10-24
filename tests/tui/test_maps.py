"""
Tests for the MAPS screen.

What must hold: the list reflects what ``scan_maps`` actually returned (scores and
shapes included, labels never upgraded from "unknown"), the selected table shows
its measured numbers and a one-line shape (not a drawing), the score filter is the
scanner's own threshold, and both exports write where they say they write.

The screen deliberately does *not* paint the table: a terminal map depends on the
font, colour depth and aspect ratio of the terminal, so the picture is written as
an SVG file instead (``P``). A test pins that decision — no ``v`` binding.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.maps import MapsScreen, sparkline
from openremap.tui.widgets.activity import ActivityBar

ROOT = Path(__file__).parent.parent.parent
M33 = ROOT / "tests/data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"


def run(body, *, size=(110, 36)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


async def _maps(pilot, app, path: Path) -> MapsScreen:
    screen = MapsScreen(str(path))
    app.push_screen(screen)
    for _ in range(600):
        if screen.tables:
            break
        await pilot.pause(0.02)
    await pilot.pause(0.2)  # let the preview land
    return screen


def _text(screen: MapsScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


class TestEmptyState:
    def test_asks_for_a_dump(self):
        async def body(pilot, app):
            app.push_screen(MapsScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#maps-file")

        assert "press F" in run(body)


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestScanAndPreview:
    def test_scan_lists_tables_with_real_numbers(self):
        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            return screen, _text(screen, "#maps-count"), len(screen.tables)

        screen, count_text, count = run(body)
        assert count >= 1
        first = screen.tables[0]
        assert first["cols"] > 0 and first["rows"] > 0
        assert 0.0 <= float(first["score"]) <= 1.0
        assert str(count) in count_text

    def test_selection_shows_measured_numbers_and_a_shape(self):
        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            for _ in range(300):
                if screen._summary:
                    break
                await pilot.pause(0.02)
            return _text(screen, "#maps-detail"), _text(screen, "#maps-profile")

        detail, profile = run(body)
        assert "min" in detail and "max" in detail and "mean" in detail
        assert "distinct" in detail
        # the shape line is a sparkline, not a picture: no colour, no cells
        assert "row means" in profile
        assert any(char in profile for char in "▁▂▃▄▅▆▇█")

    def test_unknown_labels_stay_unknown(self):
        """No upgrading a guess into a name — the classifier said unknown."""

        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            return _text(screen, "#maps-count"), screen.tables[0].get("label")

        _, label = run(body)
        assert label in (None, "", "unknown")

    def test_score_filter_cycles_and_rescans(self):
        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            before = screen.min_score
            await pilot.press("s")
            for _ in range(600):
                if screen.min_score != before:
                    break
                await pilot.pause(0.02)
            return before, screen.min_score

        before, after = run(body)
        assert after != before
        assert after < before  # the ladder loosens

    def test_save_picture_writes_an_svg_next_to_the_dump(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)

        async def body(pilot, app):
            screen = await _maps(pilot, app, copy)
            await pilot.press("p")
            await pilot.pause(0.5)
            return screen

        run(body)
        written = list(tmp_path.glob("*_map_0x*.svg"))
        assert len(written) == 1
        svg = written[0].read_text(encoding="utf-8")
        assert svg.startswith("<svg")
        assert "cells" in svg and "score" in svg  # the header carries the facts
        assert "<rect" in svg

    def test_export_writes_a_csv_next_to_the_dump(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)

        async def body(pilot, app):
            screen = await _maps(pilot, app, copy)
            await pilot.press("x")
            await pilot.pause(0.3)
            return screen

        run(body)
        written = list(tmp_path.glob("*_map_0x*.csv"))
        assert len(written) == 1
        first_line = written[0].read_text(encoding="utf-8").splitlines()[0]
        assert "," in first_line


class TestLoadingState:
    """A scan is not instant: the screen must say what it is doing.

    The 4 MB dumps take seconds, and the bug this covers was a screen that
    simply stayed blank and lifeless until the scan returned.
    """

    def test_state_and_hints_are_painted_before_the_scan_returns(self, monkeypatch):
        from textual.css.query import NoMatches

        from openremap import api

        real_call = api.call

        # A gate, not a sleep: `time.sleep(0.3)` made this a race against the
        # spinner's reveal delay, and it failed once in a full-suite run (green
        # alone, red under load). A scan that cannot return until the test says so
        # has no race to lose — the UI is provably mid-scan when it is observed.
        gate = threading.Event()

        def slow_call(name, params=None, **kwargs):
            if name == "scan_maps":
                gate.wait(timeout=10)
            return real_call(name, params, **kwargs)

        async def body(pilot, app):
            monkeypatch.setattr(api, "call", slow_call)
            screen = MapsScreen(str(M33))
            app.push_screen(screen)
            bar, observing = None, {}
            for _ in range(400):
                await pilot.pause(0.02)
                try:
                    bar = screen.query_one("#maps-activity", ActivityBar)
                except NoMatches:
                    continue
                if bar.running:
                    break
            observing = {
                "running": bool(bar and bar.running),
                "bar": str(bar.render()) if bar else "",
                "count": _text(screen, "#maps-count"),
                "hints": _text(screen, "#maps-hints"),
            }
            gate.set()  # let the scan finish now that mid-scan is recorded
            for _ in range(600):
                if screen.tables:
                    break
                await pilot.pause(0.02)
            await pilot.pause(0.1)
            observing["count_after"] = _text(screen, "#maps-count")
            observing["hints_after"] = _text(screen, "#maps-hints")
            return observing

        if not M33.exists():
            pytest.skip("test corpus absent")
        observed = run(body)
        # mid-scan: a live spinner, the size, and the keys that work
        assert observed["running"], "the spinner must be visible while scanning"
        assert "scanning" in observed["bar"]
        assert "MB" in observed["count"], "the size is known before the scan"
        assert "[F] open" in observed["hints"]
        # after the scan: the real count, and the hints still there
        assert "tables" in observed["count_after"]
        assert "[F] open" in observed["hints_after"]


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestFooterAfterScan:
    def test_hints_survive_a_finished_scan(self):
        """The footer used to be painted only in the empty state."""

        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            return _text(screen, "#maps-hints")

        hints = run(body)
        assert "[F] open" in hints
        assert "[S] score" in hints
        assert "[X] CSV" in hints  # only offered once there is a table


class TestSparkline:
    def test_one_glyph_per_value(self):
        line = sparkline([1, 2, 3, 4])
        assert len(line) == 4
        assert set(line) <= set("▁▂▃▄▅▆▇█")

    def test_rising_values_rise(self):
        line = sparkline([0, 1, 2, 3, 4, 5, 6, 7])
        assert line == "▁▂▃▄▅▆▇█"

    def test_flat_values_do_not_divide_by_zero(self):
        assert set(sparkline([5, 5, 5])) == {"▁"}

    def test_empty_is_empty(self):
        assert sparkline([]) == ""


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestNoTerminalPainting:
    def test_the_picture_keys_are_gone(self):
        """The terminal cannot draw a map honestly; the keys say so."""
        keys = {binding.key for binding in MapsScreen.BINDINGS}
        assert "v" not in keys, "no view modes on a text screen"
        assert "p" in keys, "the picture is a file"

    def test_hints_advertise_the_picture_not_a_view(self):
        async def body(pilot, app):
            screen = await _maps(pilot, app, M33)
            return _text(screen, "#maps-hints")

        hints = run(body)
        assert "[P] picture" in hints
        assert "[V]" not in hints
