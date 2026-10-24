"""
Tests for the COOK screen.

What must hold: the pair is visible before anything runs, the *cheap* facts are
true (sizes, same path), the safe mode runs first, the looser rung is offered
only when the engine refuses for repeated anchors (and says what it costs), the
report shows measured numbers only, and saving writes where the hint said.

A synthetic pair keeps these fast: the real 4 MB pair takes ~7 s per cook.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.cook import CookScreen

ROOT = Path(__file__).parent.parent
M33 = ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"


def run(body, *, size=(110, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _pair(tmp_path: Path, *, changes: int = 3) -> tuple[Path, Path]:
    """A unique-anchor pair: pseudo-random stock with a few byte edits."""
    seed = random.Random(4242)
    stock = bytearray(seed.randbytes(8192))
    tuned = bytearray(stock)
    for i in range(changes):
        tuned[512 + i * 512] ^= 0xFF
    stock_path = tmp_path / "stock.bin"
    tuned_path = tmp_path / "tuned.bin"
    stock_path.write_bytes(bytes(stock))
    tuned_path.write_bytes(bytes(tuned))
    return stock_path, tuned_path


def _text(screen: CookScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


async def _cooked(pilot, app, screen: CookScreen, timeout: float = 60.0):
    """Press Enter and wait (wall clock) for the engine to answer."""
    import time

    await pilot.press("enter")
    deadline = time.monotonic() + timeout
    while screen.report is None and time.monotonic() < deadline:
        await pilot.pause(0.05)
    await pilot.pause(0.1)
    return screen


class TestEmptyState:
    def test_asks_for_both_files(self):
        async def body(pilot, app):
            app.push_screen(CookScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#cook-stock"), _text(app.screen, "#cook-tuned")

        stock, tuned = run(body)
        assert "choose a file" in stock
        assert "choose a file" in tuned

    def test_cook_is_not_offered_without_both_files(self):
        async def body(pilot, app):
            screen = CookScreen()
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-hints")

        assert "[Enter]" not in run(body)

    def test_arriving_from_identify_keeps_the_file(self, tmp_path):
        stock_path, _ = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-stock"), _text(screen, "#cook-tuned")

        stock, tuned = run(body)
        assert "stock.bin" in stock
        assert "choose a file" in tuned


class TestCheapChecks:
    def test_same_size_is_stated_before_cooking(self, tmp_path):
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-basis")

        basis = run(body)
        assert "same size" in basis
        assert "any dump of this family" in basis

    def test_different_sizes_are_flagged_immediately(self, tmp_path):
        stock_path, _ = _pair(tmp_path)
        other = tmp_path / "bigger.bin"
        other.write_bytes(b"\x00" * 16384)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(other))
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-basis")

        basis = run(body)
        assert "different sizes" in basis
        assert "same image" in basis

    def test_the_same_file_twice_is_called_out(self, tmp_path):
        stock_path, _ = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-basis")

        assert "same file is selected twice" in run(body)


class TestWhatTheScreenSaysBeforeCooking:
    def test_it_says_what_to_press(self, tmp_path):
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.1)
            return _text(screen, "#cook-body")

        text = run(body)
        assert "[Enter]" in text
        assert "to cook this pair" in text

    def test_the_mode_is_not_presented_as_a_choice(self, tmp_path):
        """It follows from the files — advertising it as a setting would be a lie."""
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.1)
            return _text(screen, "#cook-body")

        assert "mode follows from the files" in run(body)

    def test_with_one_file_it_asks_for_the_other(self, tmp_path):
        stock_path, _ = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.1)
            return _text(screen, "#cook-body")

        text = run(body)
        assert "[T]" in text and "tuned" in text

    def test_both_match_keys_are_read_in_the_background(self):
        """The same fact cook checks, shown before the run — when a real pair exists."""
        if not M33.exists():
            pytest.skip("test corpus absent")

        async def body(pilot, app):
            import shutil
            import tempfile

            folder = Path(tempfile.mkdtemp())
            tuned = folder / "tuned.bin"
            shutil.copy(M33, tuned)
            data = bytearray(tuned.read_bytes())
            data[4096] ^= 0xFF
            tuned.write_bytes(bytes(data))

            screen = CookScreen(stock=str(M33), tuned=str(tuned))
            app.push_screen(screen)
            for _ in range(400):
                await pilot.pause(0.02)
                if screen.identity:
                    break
            return screen.identity, _text(screen, "#cook-basis")

        identity, basis = run(body)
        assert identity.get("stock") and identity.get("tuned")
        assert "same firmware" in basis


class TestCook:
    def test_a_pair_with_unique_anchors_produces_a_recipe(self, tmp_path):
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            await _cooked(pilot, app, screen)
            footer = _text(screen, "#cook-hints")
            return screen.recipe, screen.report, _text(screen, "#cook-body"), footer

        recipe, report, body_text, footer = run(body)
        assert recipe is not None
        assert "RECIPE BUILT" in body_text
        assert report["result"]["produced"] is True
        # the numbers on screen are the engine's numbers
        assert str(report["counts"]["instructions"]) in body_text
        assert report["contract"]["identity_floor"] == "any"
        assert "applies to any dump of this family" in body_text
        # the raw contract value stays visible for whoever wants it
        assert "identity_floor any" in body_text
        # provenance of both inputs
        assert "SOURCE" in body_text and "TUNE" in body_text
        # and the unsaved state is a warning of its own, not a footnote
        assert "NOT SAVED" in footer
        assert "to write" in footer

    def test_identical_files_are_refused_in_plain_words(self, tmp_path):
        stock_path, _ = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            await _cooked(pilot, app, screen)
            return screen.recipe, _text(screen, "#cook-body")

        recipe, body_text = run(body)
        assert "NOT COOKED" in body_text
        assert "identical" in body_text
        assert recipe is None

    def test_the_looser_rung_is_offered_only_after_a_repeated_anchor(self, tmp_path):
        """Blank flash repeats an anchor — strict refuses, forced is offered."""
        stock_path = tmp_path / "stock.bin"
        tuned_path = tmp_path / "tuned.bin"
        stock_path.write_bytes(b"\x00" * 4096)
        tuned = bytearray(4096)
        tuned[1024] = 0xAA
        tuned_path.write_bytes(bytes(tuned))

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            hints_before = _text(screen, "#cook-hints")
            await _cooked(pilot, app, screen)
            rule = ((screen.report or {}).get("refusal") or {}).get("rule")
            return hints_before, rule, _text(screen, "#cook-hints")

        hints_before, rule, hints_after = run(body)
        assert "[M]" not in hints_before, "no escalation before the engine speaks"
        assert rule == "unique"
        assert "[M]" in hints_after
        assert "same firmware" in hints_after

    def test_a_different_firmware_refusal_shows_both_identities(self, tmp_path, monkeypatch):
        stock_path, tuned_path = _pair(tmp_path)
        from openremap.core.services.recipes.produce import builder

        keys = iter(["EDC16::AAA", "SIMOS::BBB"])
        monkeypatch.setattr(
            builder, "identify_ecu", lambda data, filename="x": {"match_key": next(keys)}
        )

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            await _cooked(pilot, app, screen)
            return _text(screen, "#cook-body")

        body_text = run(body)
        assert "different firmware" in body_text
        assert "EDC16::AAA" in body_text and "SIMOS::BBB" in body_text


class TestVerify:
    """`[V]` audits the triplet: does this recipe reproduce the tuned file?"""

    def test_the_key_appears_only_after_a_recipe_exists(self, tmp_path):
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.2)
            before = _text(screen, "#cook-hints")
            await _cooked(pilot, app, screen)
            return before, _text(screen, "#cook-hints")

        before, after = run(body)
        assert "[V]" not in before
        assert "[V]" in after and "verify" in after

    def test_verify_proves_the_three_files_agree(self, tmp_path, monkeypatch):
        def _identify(data, filename="x"):
            return {"match_key": "TEST::SAME", "manufacturer": "Test", "ecu_family": "TEST"}

        monkeypatch.setattr(
            "openremap.core.services.recipes.produce.builder.identify_ecu", _identify
        )
        monkeypatch.setattr(
            "openremap.core.services.recipes.apply.engine.identify_ecu", _identify
        )
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await _cooked(pilot, app, screen)
            await pilot.press("v")
            for _ in range(600):
                await pilot.pause(0.05)
                if screen.verification is not None:
                    break
            await pilot.pause(0.3)
            return screen.verification, _text(screen, "#cook-body")

        verification, body_text = run(body)
        assert verification is not None
        assert verification["clean"] is True
        assert verification["provenance"]["ok"] is True
        assert "VERIFIED" in body_text
        assert "reproduces the tuned file" in body_text


class TestSave:
    def test_save_writes_the_recipe_where_the_hint_said(self, tmp_path, monkeypatch):
        stock_path, tuned_path = _pair(tmp_path)
        recipes = tmp_path / "recipes"

        from openremap.tui import paths

        monkeypatch.setattr(paths, "subdir", lambda name: (recipes.mkdir(parents=True, exist_ok=True), recipes)[1])

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            await _cooked(pilot, app, screen)
            hints = _text(screen, "#cook-hints")
            await pilot.press("s")
            await pilot.pause(0.3)
            return screen.saved_to, hints

        saved_to, not_saved_hint = run(body)
        # before the save the footer warns, and names the file it will write
        assert "NOT SAVED" in not_saved_hint
        assert "tuned.remap" in not_saved_hint
        written = recipes / "tuned.remap"
        assert written.exists()
        recipe = json.loads(written.read_text(encoding="utf-8"))
        assert recipe["schema_version"] == "5.0"
        assert recipe["contract"]["identity_floor"] == "any"
        assert saved_to == written

    def test_an_existing_recipe_needs_a_second_press(self, tmp_path, monkeypatch):
        """Never silently replace a file: the first [S] asks, the second writes."""
        stock_path, tuned_path = _pair(tmp_path)
        recipes = tmp_path / "recipes"
        recipes.mkdir()
        existing = recipes / "tuned.remap"
        existing.write_text("{}", encoding="utf-8")

        from openremap.tui import paths

        monkeypatch.setattr(paths, "subdir", lambda name: recipes)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            await _cooked(pilot, app, screen)
            await pilot.press("s")
            await pilot.pause(0.2)
            first = (screen.saved_to, _text(screen, "#cook-hints"), _text(screen, "#cook-body"))
            await pilot.press("s")
            await pilot.pause(0.3)
            return first, screen.saved_to

        (saved_first, hints, _body_text), saved_second = run(body)
        assert saved_first is None, "the first press must not write"
        assert "EXISTS" in hints and "overwrite" in hints
        assert saved_second == existing

    def test_no_save_offer_before_a_recipe_exists(self, tmp_path):
        stock_path, tuned_path = _pair(tmp_path)

        async def body(pilot, app):
            screen = CookScreen(stock=str(stock_path), tuned=str(tuned_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            return _text(screen, "#cook-hints")

        assert "[S]" not in run(body)
