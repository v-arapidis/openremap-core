"""
Tests for the CONTRIBUTE screen.

What must hold: the safe step (measure) works with nothing but a dump, the
screen keeps saying that collisions were **not** checked while that is true,
naming the family is what unlocks rendering, the review bar is shown verbatim,
and writing artifacts puts the four generated files in the workspace — never the
contributor's dump.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openremap.tui import paths
from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.contribute import ContributeScreen

ROOT = Path(__file__).parent.parent
M33 = (ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin").resolve()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    folder = tmp_path / "OpenRemap"
    folder.mkdir()
    monkeypatch.setattr(paths, "workspace", lambda: folder)

    def subdir(name: str) -> Path:
        target = folder / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(paths, "subdir", subdir)
    return folder


def run(body, *, size=(112, 34)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


def _text(screen: ContributeScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


async def _measured(pilot, app, screen, timeout: float = 60.0):
    import time

    await pilot.press("enter")
    deadline = time.monotonic() + timeout
    while screen.measurement is None and time.monotonic() < deadline:
        await pilot.pause(0.05)
    await pilot.pause(0.2)
    return screen


async def _name(pilot, app, screen, key: str, value: str) -> None:
    await pilot.press(key)
    await pilot.pause(0.4)
    prompt = app.screen
    prompt.query_one("#prompt-input").value = value
    await pilot.press("enter")
    await pilot.pause(0.4)


class TestEmptyState:
    def test_it_promises_the_dumps_stay_local(self):
        async def body(pilot, app):
            app.push_screen(ContributeScreen())
            await pilot.pause(0.2)
            return _text(app.screen, "#contribute-body"), _text(app.screen, "#contribute-hints")

        body_text, hints = run(body)
        assert "never leave this machine" in body_text
        assert "[Enter]" not in hints, "nothing to measure yet"
        assert "[P]" not in hints, "nothing to render yet"


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestMeasure:
    def test_a_dump_is_enough_to_measure(self):
        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            await _measured(pilot, app, screen)
            return _text(screen, "#contribute-body"), _text(screen, "#contribute-hints")

        body_text, hints = run(body)
        assert "MEASURED" in body_text
        assert "signature candidates" in body_text
        assert "already claimed" in body_text or "not claimed" in body_text
        assert "[Enter] measure" in hints

    def test_it_says_collisions_were_not_checked(self):
        """The one thing measuring does *not* prove must stay on screen."""

        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            await _measured(pilot, app, screen)
            return screen.measurement, _text(screen, "#contribute-body")

        measurement, body_text = run(body)
        assert measurement["mode"] == "measure"
        assert "Collisions" in body_text and "NOT checked" in body_text

    def test_one_dump_is_called_out_as_unproven(self):
        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            await pilot.pause(0.1)
            return _text(screen, "#contribute-dumps")

        line = run(body)
        assert "cannot prove a marker is stable" in line


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestRenderAndWrite:
    def test_naming_the_family_is_what_unlocks_rendering(self, workspace):
        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            before = _text(screen, "#contribute-hints")
            await _name(pilot, app, screen, "b", "Bosch")
            await _name(pilot, app, screen, "t", "M9.9")
            return before, _text(screen, "#contribute-hints"), screen.brand, screen.family

        before, after, brand, family = run(body)
        assert "[P]" not in before
        assert "[P] render" in after
        assert (brand, family) == ("Bosch", "M9.9")

    def test_rendering_shows_the_review_bar(self, workspace):
        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            await _name(pilot, app, screen, "b", "Bosch")
            await _name(pilot, app, screen, "t", "M9.9")
            await pilot.press("p")
            for _ in range(1200):
                await pilot.pause(0.05)
                if screen.prepared is not None:
                    break
            await pilot.pause(0.3)
            return screen.prepared, _text(screen, "#contribute-body")

        prepared, body_text = run(body)
        assert prepared is not None
        checks = prepared["verification"]["checks"]
        assert checks, "the review bar ran"
        for check in checks:
            assert str(check["name"]) in body_text
        assert "RENDERED" in body_text

    def test_writing_puts_four_artifacts_in_the_workspace_and_never_the_dump(
        self, workspace
    ):
        async def body(pilot, app):
            screen = ContributeScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            screen.dumps.append(str(M33))
            screen._paint()
            await _name(pilot, app, screen, "b", "Bosch")
            await _name(pilot, app, screen, "t", "M9.9")
            await pilot.press("p")
            for _ in range(1200):
                await pilot.pause(0.05)
                if screen.prepared is not None:
                    break
            await pilot.pause(0.2)
            await pilot.press("w")
            await pilot.pause(0.5)
            return dict(screen.written)

        written = run(body)
        assert set(written) == {"extractor", "test", "report", "recipe"}
        folder = workspace / "contributions" / "Bosch-M9.9"
        for key, target in written.items():
            assert target.exists() and target.parent == folder, key
        # the contributor's binary is never copied anywhere
        copied = [p for p in workspace.rglob("*") if p.suffix in (".bin", ".ori", ".hex")]
        assert copied == []
