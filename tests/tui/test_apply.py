"""
Tests for the APPLY screen.

What must hold: the two inputs are readable before anything runs, the *strict*
rung is what Enter means, the looser rung is offered only when the failure's own
rule allows it (and says what it gives up), the result reports the engine's
checks verbatim, the strongest claim on the screen is verifiable (the patched
bytes equal the file the recipe was cooked from), and nothing is written until
``W``.

A small synthetic pair keeps these quick; the 4 MB pair is used once for the
byte-identical claim, where the real fixture matters.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from pathlib import Path

import pytest

from openremap.api import call
from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.apply import ApplyScreen, _escalation_for

ROOT = Path(__file__).parent.parent
TUNE_STOCK = ROOT / "data/tune/original.bin"
TUNE_TUNED = ROOT / "data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"


def run(body, *, size=(110, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _identify_as(monkeypatch, key: str = "TEST::SAME") -> None:
    """Make both sides identify as one firmware, so the strict rung can run."""

    def _identify(data, filename="x"):
        return {"match_key": key, "manufacturer": "Test", "ecu_family": "TEST"}

    monkeypatch.setattr(
        "openremap.core.services.recipes.produce.builder.identify_ecu", _identify
    )
    monkeypatch.setattr(
        "openremap.core.services.recipes.apply.engine.identify_ecu", _identify
    )


def _pair(tmp_path: Path) -> tuple[Path, Path, dict]:
    """A stock/tuned pair plus the recipe cooked from it (both small)."""
    seed = random.Random(99)
    stock = bytearray(seed.randbytes(8192))
    tuned = bytearray(stock)
    for i in range(3):
        tuned[1024 + i * 1024] ^= 0xFF
    stock_path = tmp_path / "stock.bin"
    tuned_path = tmp_path / "tuned.bin"
    stock_path.write_bytes(bytes(stock))
    tuned_path.write_bytes(bytes(tuned))
    recipe = call(
        "cook_strict",
        {"stock_path": str(stock_path), "tuned_path": str(tuned_path)},
    )["recipe"]
    recipe_path = tmp_path / "pair.remap"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    return stock_path, tuned_path, recipe


def _text(screen: ApplyScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


async def _applied(pilot, app, screen: ApplyScreen, timeout: float = 60.0):
    import time

    await pilot.press("enter")
    deadline = time.monotonic() + timeout
    while screen.report is None and time.monotonic() < deadline:
        await pilot.pause(0.05)
    await pilot.pause(0.15)
    return screen


class TestForm:
    def test_asks_for_both_inputs(self):
        async def body(pilot, app):
            app.push_screen(ApplyScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#apply-recipe"), _text(app.screen, "#apply-target")

        recipe, target = run(body)
        assert "choose a .remap" in recipe
        assert "choose the binary" in target

    def test_no_apply_key_without_inputs(self):
        async def body(pilot, app):
            app.push_screen(ApplyScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#apply-hints")

        assert "[Enter]" not in run(body)

    def test_recipe_facts_are_read_from_the_file(self, tmp_path):
        stock_path, _, recipe = _pair(tmp_path)
        recipe_path = tmp_path / "pair.remap"

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(recipe_path), target=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.3)
            return _text(screen, "#apply-recipe"), screen.recipe

        line, info = run(body)
        assert f"{len(recipe['instructions'])} instr" in line
        assert "floor any" in line
        assert info is not None and info["contract"]["identity_floor"] == "any"

    def test_an_unreadable_recipe_is_a_state_not_a_crash(self, tmp_path):
        bad = tmp_path / "broken.remap"
        bad.write_text("{\"type\": \"something-else\"}", encoding="utf-8")
        stock_path, _, _ = _pair(tmp_path)

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(bad), target=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.3)
            return screen.recipe, _text(screen, "#apply-basis"), _text(screen, "#apply-body")

        recipe, basis, body_text = run(body)
        assert recipe is None
        assert "cannot be read" in basis
        assert "[R]" in body_text


class TestApplying:
    def test_enter_uses_the_strict_rung(self, tmp_path, monkeypatch):
        _identify_as(monkeypatch)
        stock_path, _, _ = _pair(tmp_path)
        recipe_path = tmp_path / "pair.remap"

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(recipe_path), target=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await _applied(pilot, app, screen)
            return screen.rung, screen.applied, screen.report, _text(
                screen, "#apply-body"
            ), _text(screen, "#apply-hints")

        rung, applied, report, body_text, hints = run(body)
        assert rung == "strict"
        assert applied is True
        assert report is not None and report["result"]["applied"] is True
        assert "APPLIED" in body_text
        # the engine's own numbers, not ours
        assert str(report["instructions"]["applied"]) in body_text
        # every check the engine ran is on screen, with its rule id
        for check in report["checks"]:
            assert str(check["id"]) in body_text
        # and the output is not written until asked
        assert "NOT WRITTEN" in hints and "[W]" in hints
        # "APPLIED" is about the recipe; the file can still be unflashable. The
        # screen says which of the three honest answers the measurement gave.
        assert "CHECKSUMS" in body_text
        assert "not corrected" in body_text
        assert "openremap checksum" in body_text
        # and the boundary of what the file may be used for, in its own colour
        assert "DISCLAIMER" in body_text
        assert "dedicated tuner shop" in body_text
        assert "everyday car" in body_text

    def test_writing_puts_the_bytes_in_the_workspace(self, tmp_path, monkeypatch):
        _identify_as(monkeypatch)
        stock_path, tuned_path, _ = _pair(tmp_path)
        recipes = tmp_path / "tunes"

        from openremap.tui import paths

        monkeypatch.setattr(
            paths, "subdir", lambda name: (recipes.mkdir(parents=True, exist_ok=True), recipes)[1]
        )

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(tmp_path / "pair.remap"), target=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await _applied(pilot, app, screen)
            await pilot.press("w")
            await pilot.pause(0.3)
            return screen.saved_to, _text(screen, "#apply-hints")

        saved_to, hints = run(body)
        written = recipes / "stock_tuned.bin"
        assert saved_to == written
        assert written.read_bytes() == tuned_path.read_bytes()
        assert "SAVED" in hints


@pytest.mark.skipif(not TUNE_STOCK.exists(), reason="test corpus absent")
class TestTheByteIdenticalClaim:
    def test_the_result_matches_the_file_the_recipe_came_from(self, tmp_path):
        recipe = call(
            "cook_strict",
            {"stock_path": str(TUNE_STOCK), "tuned_path": str(TUNE_TUNED)},
        )["recipe"]
        recipe_path = tmp_path / "real.remap"
        recipe_path.write_text(json.dumps(recipe), encoding="utf-8")

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(recipe_path), target=str(TUNE_STOCK))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await _applied(pilot, app, screen)
            return screen.output_sha256, _text(screen, "#apply-body")

        output_sha, body_text = run(body)
        expected = hashlib.sha256(TUNE_TUNED.read_bytes()).hexdigest()
        assert output_sha == expected
        assert "byte-identical" in body_text


class TestRecipeDetail:
    def test_d_opens_the_instruction_list(self, tmp_path, monkeypatch):
        _identify_as(monkeypatch)
        stock_path, _, recipe = _pair(tmp_path)
        recipe_path = tmp_path / "pair.remap"

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(recipe_path), target=str(stock_path))
            app.push_screen(screen)
            await pilot.pause(0.4)
            hints = _text(screen, "#apply-hints")
            await pilot.press("d")
            await pilot.pause(0.6)
            modal = app.screen
            table = modal.query_one("#recipe-table")
            rows = [tuple(str(c) for c in table.get_row_at(i)) for i in range(table.row_count)]
            facts = str(modal.query_one("#recipe-facts").render())
            return hints, type(modal).__name__, rows, facts

        hints, name, rows, facts = run(body)
        assert "[D]" in hints
        assert name == "RecipeDetailScreen"
        assert len(rows) == len(recipe["instructions"])
        # offset, bytes, region per row — and the facts line names the source
        assert rows[0][1].startswith("0x")
        assert "instructions" in facts and "schema" in facts


def test_the_recipe_detail_closes_on_escape(tmp_path, monkeypatch):
    """The modal advertised `Esc close` and had no action at all — a dead key."""
    _identify_as(monkeypatch)
    stock_path, _, _ = _pair(tmp_path)

    async def body(pilot, app):
        screen = ApplyScreen(recipe=str(tmp_path / "pair.remap"), target=str(stock_path))
        app.push_screen(screen)
        await pilot.pause(0.4)
        await pilot.press("d")
        await pilot.pause(0.5)
        opened = type(app.screen).__name__
        await pilot.press("escape")
        await pilot.pause(0.4)
        return opened, type(app.screen).__name__

    opened, closed_to = run(body)
    assert opened == "RecipeDetailScreen"
    assert closed_to == "ApplyScreen"


class TestRefusalLadder:
    def test_strict_refusal_offers_the_relaxation_that_answers_it(self, tmp_path):
        stock_path, _, _ = _pair(tmp_path)
        other = tmp_path / "other.bin"
        other.write_bytes(bytes(random.Random(7).randbytes(stock_path.stat().st_size)))

        async def body(pilot, app):
            screen = ApplyScreen(recipe=str(tmp_path / "pair.remap"), target=str(other))
            app.push_screen(screen)
            await pilot.pause(0.2)
            await _applied(pilot, app, screen)
            return screen.report, _text(screen, "#apply-body"), _text(screen, "#apply-hints")

        report, body_text, hints = run(body)
        rule = (report or {}).get("refusal", {}).get("check")
        assert rule is not None
        assert "REFUSED" in body_text
        # whatever the rule, an offered key must be one the screen actually has
        offered = [key for key in ("[U]", "[S]", "[A]") if key in hints]
        if offered:
            assert "gives up" in body_text

    def test_every_offer_is_strictly_looser_and_explained(self):
        """A rung may not offer itself, and every offer says what it costs."""
        from openremap.tui.screens.apply import _ESCALATION

        assert _ESCALATION, "the ladder must not be empty"
        for (failed_rung, rule), (key, next_rung, what, cost) in _ESCALATION.items():
            assert failed_rung in ("strict", "shift", "unique")
            assert key in ("u", "s", "a")
            assert next_rung != failed_rung, f"{failed_rung} + {rule} offered itself"
            assert next_rung in ("shift", "unique", "any")
            assert what and cost

    def test_an_unknown_refusal_offers_nothing_looser(self):
        assert _escalation_for("unique", "Γ5") is None
        assert _escalation_for("any", "Γ7") is None
