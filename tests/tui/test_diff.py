"""
Tests for the DIFF screen.

What must hold: the pair is readable before anything runs, the summary never lets
the explained part of a change stand in for the whole, every filter keeps exactly
what its label says, the delta grid shows stock / tuned / Δ per cell, the hex view
shows both sides of a region no table accounts for, and ``Esc`` unwinds one level
at a time (sub-view → list → back).

Most tests inject a measured result rather than running the 5-second scan: the
screen's job is what it does with numbers, and one end-to-end test proves the scan
still feeds it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.diff import FILTERS, DiffScreen

ROOT = Path(__file__).parent.parent
TUNE_STOCK = ROOT / "data/tune/original.bin"
TUNE_TUNED = ROOT / "data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"


def run(body, *, size=(120, 40)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _text(screen: DiffScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


def _result() -> dict:
    """A measured-result shape, with one changed axis table, one no-axis table,
    one unchanged table, one reshaped entry per side and two raw regions."""
    return {
        "stock_size": 0x400000,
        "tuned_size": 0x400000,
        "stock_tables": 10,
        "tuned_tables": 10,
        "stock_tables_hidden": 2,
        "tuned_tables_hidden": 2,
        "matched_count": 9,
        "above_threshold": 9,
        "only_in_stock_count": 1,
        "only_in_tuned_count": 1,
        "unidentified_changed_count": 2,
        "scan_seconds": 1.0,
        "groups": [
            {
                "id": "A",
                "count": 1,
                "cols": 4,
                "rows": 3,
                "cell_width": 2,
                "byte_order": "little",
                "x_axis": [100, 200, 300, 400],
                "y_axis": [10, 20, 30],
            },
            {
                "id": "B",
                "count": 2,
                "cols": 2,
                "rows": 2,
                "cell_width": 2,
                "byte_order": "little",
                "x_axis": None,
                "y_axis": None,
            },
        ],
        "matches": [
            {
                "offset_stock": 0x1000,
                "offset_tuned": 0x1000,
                "cols": 4,
                "rows": 3,
                "cell_width": 2,
                "byte_order": "little",
                "stride": None,
                "realigned": False,
                "suspicious": False,
                "max_abs": 500.0,
                "avg_abs": 250.0,
                "max_pct": 20.0,
                "avg_pct": 10.0,
                "changed_cells": 6,
                "total_cells": 12,
                "group": 0,
            },
            {
                "offset_stock": 0x3000,
                "offset_tuned": 0x3000,
                "cols": 2,
                "rows": 2,
                "cell_width": 2,
                "byte_order": "little",
                "stride": None,
                "realigned": True,
                "suspicious": False,
                "max_abs": 100.0,
                "avg_abs": 50.0,
                "max_pct": -100.0,
                "avg_pct": 100.0,
                "changed_cells": 4,
                "total_cells": 4,
                "group": 1,
            },
            {
                "offset_stock": 0x4000,
                "offset_tuned": 0x4000,
                "cols": 2,
                "rows": 2,
                "cell_width": 2,
                "byte_order": "little",
                "stride": None,
                "realigned": False,
                "suspicious": False,
                "max_abs": 0.0,
                "avg_abs": 0.0,
                "max_pct": 0.0,
                "avg_pct": 0.0,
                "changed_cells": 0,
                "total_cells": 4,
                "group": 1,
            },
        ],
        "only_in_stock": [
            {"offset": 0x5000, "cols": 2, "rows": 2, "cell_width": 2, "byte_order": "little"}
        ],
        "only_in_tuned": [
            {"offset": 0x6000, "cols": 3, "rows": 2, "cell_width": 2, "byte_order": "little"}
        ],
        "unidentified_changed": [
            {"offset": 0x7000, "size": 436},
            {"offset": 0x7100, "size": 39},
        ],
    }


def _screen_with_result(tmp_path: Path, monkeypatch) -> DiffScreen:
    stock = tmp_path / "stock.bin"
    tuned = tmp_path / "tuned.bin"
    stock.write_bytes(b"\x00" * 0x8000)
    tuned.write_bytes(b"\x00" * 0x8000)
    screen = DiffScreen(stock=str(stock), tuned=str(tuned))
    screen.result = _result()
    screen.scan_ms = 1234.0
    screen.regions = sorted(
        screen.result["unidentified_changed"], key=lambda r: -r["size"]
    )
    return screen


class TestInputs:
    def test_asks_for_both_files(self, tmp_path):
        async def body(pilot, app):
            screen = DiffScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            return (
                _text(screen, "#diff-stock"),
                _text(screen, "#diff-tuned"),
                _text(screen, "#diff-body"),
            )

        stock, tuned, body_text = run(body)
        assert "[S]" in stock and "choose a file" in stock
        assert "[T]" in tuned and "choose a file" in tuned
        # and it says what to press first
        assert "press [S]" in body_text

    def test_the_pair_can_arrive_prefilled(self, tmp_path):
        """COOK hands its pair over — the user must not pick the same files twice."""
        stock = tmp_path / "stock.bin"
        tuned = tmp_path / "tuned.bin"
        stock.write_bytes(b"\x00" * 0x1000)
        tuned.write_bytes(b"\x00" * 0x1000)

        async def body(pilot, app):
            screen = DiffScreen(stock=str(stock), tuned=str(tuned))
            app.push_screen(screen)
            await pilot.pause(0.4)
            return _text(screen, "#diff-basis"), _text(screen, "#diff-hints")

        basis, hints = run(body)
        assert "same size" in basis
        assert "[Enter]" in hints and "compare" in hints

    def test_the_same_file_twice_is_refused_before_the_scan(self, tmp_path):
        stock = tmp_path / "stock.bin"
        stock.write_bytes(b"\x00" * 0x1000)

        async def body(pilot, app):
            screen = DiffScreen(stock=str(stock), tuned=str(stock))
            app.push_screen(screen)
            await pilot.pause(0.4)
            return _text(screen, "#diff-basis")

        assert "same file is selected twice" in run(body)


class TestSummary:
    def test_the_unexplained_part_is_as_visible_as_the_explained(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "CHANGED" in text and "2 tables · 10 cells" in text
        assert "NOT IN A TABLE" in text and "2 regions" in text
        assert "largest 436 B" in text
        assert "RESHAPED" in text and "1 stock · 1 tuned" in text
        assert "UNCHANGED" in text and "7 tables" in text

    def test_the_region_size_is_never_called_a_byte_count(self, tmp_path, monkeypatch):
        """A region is an extent, so its size is not "changed bytes"."""
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "B changed" not in text
        assert "largest 436 B" in text

    def test_the_hidden_tables_are_stated_not_silently_dropped(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "outside the calibration region" in text


class TestFilters:
    def test_each_filter_keeps_exactly_what_its_label_says(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            counts = {}
            for _ in range(len(FILTERS) + 1):
                counts[screen.filter] = len(screen.rows())
                await pilot.press("f")
                await pilot.pause(0.1)
            return counts

        counts = run(body)
        assert counts["changed"] == 2  # the unchanged table is not listed
        assert counts["realigned"] == 1
        assert counts["no axis"] == 1
        assert counts["all"] == 3

    def test_a_no_axis_table_is_flagged_in_its_row(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        assert "no axis" in run(body)


class TestDetail:
    def test_the_grid_shows_stock_tuned_and_delta(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        sides = {
            "stock": [[10, 20, 30, 40], [11, 21, 31, 41], [12, 22, 32, 42]],
            "tuned": [[15, 25, 35, 45], [16, 26, 36, 46], [17, 27, 37, 47]],
        }

        def fake_call(name, params, *args, **kwargs):
            if name != "map_grid":
                raise AssertionError(f"unexpected call: {name}")
            # Keyed by the exact path, never by a substring: pytest's tmp_path
            # carries this test's own name ("...stock_tuned0"), so `"stock" in
            # path` is true for both sides and the delta silently reads zero.
            side = "stock" if params["path"] == screen.stock else "tuned"
            assert params["path"] in (screen.stock, screen.tuned), params["path"]
            return {"rows": sides[side], "summary": {}}

        monkeypatch.setattr("openremap.api.call", fake_call)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        # both sides, per cell, plus the delta row
        assert "stock" in text and "tuned" in text
        assert "+5" in text  # 15 - 10 on the first cell
        # the measured breakpoints, not raw indices
        assert "100" in text and "10" in text

    def test_the_no_axis_table_says_its_labels_are_indices(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)
        screen.filter = "no axis"
        screen.cursor = 0

        def fake_call(name, params, *args, **kwargs):
            return {"rows": [[1, 2], [3, 4]], "summary": {}}

        monkeypatch.setattr("openremap.api.call", fake_call)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("d")
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        assert "cell indices" in run(body)


class TestUnexplained:
    def test_regions_are_listed_largest_first(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("u")
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "0x00007000" in text and "436 B" in text
        assert text.index("0x00007000") < text.index("0x00007100")

    def test_enter_shows_both_sides_of_the_region(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        def fake_call(name, params, *args, **kwargs):
            assert name == "hex_region"
            if "stock" in params["path"]:
                return {"hex": "11223344", "offset": 0x7000, "length": 4}
            return {"hex": "11223044", "offset": 0x7000, "length": 4}

        monkeypatch.setattr("openremap.api.call", fake_call)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("u")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "11 22 33 44" in text  # stock
        assert "30" in text  # tuned's changed byte

    def test_u_is_not_advertised_when_nothing_is_unexplained(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)
        screen.result["unidentified_changed"] = []
        screen.result["unidentified_changed_count"] = 0
        screen.regions = []

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            return _text(screen, "#diff-hints")

        hints = run(body)
        assert "[U]" not in hints


class TestReshaped:
    def test_both_sides_are_listed(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("m")
            await pilot.pause(0.2)
            return _text(screen, "#diff-body")

        text = run(body)
        assert "Only in stock — 1" in text and "0x00005000" in text
        assert "Only in tuned — 1" in text and "0x00006000" in text


class TestNavigation:
    def test_escape_unwinds_one_level_at_a_time(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("m")
            await pilot.pause(0.1)
            after_m = screen.view
            await pilot.press("escape")
            await pilot.pause(0.1)
            back_to_list = screen.view
            await pilot.press("escape")
            await pilot.pause(0.2)
            return after_m, back_to_list, screen.is_attached

        after_m, back_to_list, attached = run(body)
        assert after_m == "reshaped"
        assert back_to_list == "tables"
        assert attached is False

    def test_the_cursor_moves_through_the_list(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            first = screen.cursor
            await pilot.press("down")
            await pilot.pause(0.1)
            moved = screen.cursor
            await pilot.press("up")
            await pilot.pause(0.1)
            return first, moved, screen.cursor

        first, moved, back = run(body)
        assert (first, moved, back) == (0, 1, 0)


class TestBridge:
    def test_c_hands_the_pair_to_cook(self, tmp_path, monkeypatch):
        screen = _screen_with_result(tmp_path, monkeypatch)

        async def body(pilot, app):
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("c")
            await pilot.pause(0.3)
            top = app.screen
            return type(top).__name__, getattr(top, "stock", None), getattr(top, "tuned", None)

        name, stock, tuned = run(body)
        assert name == "CookScreen"
        assert stock == screen.stock and tuned == screen.tuned


@pytest.mark.skipif(not TUNE_STOCK.exists(), reason="test corpus absent")
class TestRealPair:
    def test_compare_finds_the_changes_and_feeds_the_summary(self):
        """One end-to-end run: the scan's own numbers reach the screen."""

        async def body(pilot, app):
            screen = DiffScreen(stock=str(TUNE_STOCK), tuned=str(TUNE_TUNED))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await pilot.press("enter")
            import time

            deadline = time.monotonic() + 180
            while screen.result is None and time.monotonic() < deadline:
                await pilot.pause(0.1)
            await pilot.pause(0.3)
            return screen, _text(screen, "#diff-body")

        screen, text = run(body)
        assert screen.result is not None
        assert screen.result["matched_count"] > 1000
        assert "CHANGED" in text
        # the real pair has 34 changed tables and 69 unexplained regions
        assert len(screen.rows()) >= 30
        assert len(screen.regions) >= 60
