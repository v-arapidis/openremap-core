"""
Every screen's key bar must be *visible*.

This exists because of a real bug: three screens set ``height: 1`` together with
a top border, so the border took the only row and the text area was zero rows
tall. The keys were in the DOM, in the bindings, and in ``.render()`` — and
invisible on screen. Unit tests read renderables, so nothing caught it; a CSS
height did. Hence a test that measures the *content* box, not the renderable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.apply import ApplyScreen
from openremap.tui.screens.contribute import ContributeScreen
from openremap.tui.screens.cook import CookScreen
from openremap.tui.screens.identify import IdentifyScreen
from openremap.tui.screens.inspect import InspectScreen
from openremap.tui.screens.maps import MapsScreen

M33 = Path(__file__).parent.parent / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"

#: (screen factory, hint widget selector, text that must appear in it)
BARS = [
    (lambda path: CookScreen(stock=path), "#cook-hints", "stock file"),
    (lambda path: ApplyScreen(target=path), "#apply-hints", "recipe"),
    (lambda path: MapsScreen(path), "#maps-hints", "[F] open"),
    (lambda path: IdentifyScreen(path), "#identify-next", "inspect"),
    (lambda path: InspectScreen(path), "#inspect-next", "re-run"),
]


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
@pytest.mark.parametrize("factory,selector,needle", BARS, ids=[b[1] for b in BARS])
def test_the_key_bar_has_a_row_to_draw_in(factory, selector, needle):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.05)
            screen = factory(str(M33))
            app.push_screen(screen)
            await pilot.pause(0.4)
            bar = screen.query_one(selector)
            return bar.size.height, str(bar.render())

    height, text = asyncio.run(scenario())
    assert height >= 1, f"{selector} has no content row — the keys are invisible"
    assert needle in text, f"{selector} does not advertise its keys"


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
def test_every_key_bar_fits_an_eighty_column_terminal():
    """A hint that is clipped is a hint nobody can read.

    The bars are hand-written strings, so they grow silently: identify's ran to
    120 columns and maps' to 87 before this test existed.
    """
    import re

    from openremap.tui.screens.apply import ApplyScreen
    from openremap.tui.screens.batch import BatchScreen
    from openremap.tui.screens.cook import CookScreen
    from openremap.tui.screens.help import HelpScreen
    from openremap.tui.screens.home import HomeScreen
    from openremap.tui.screens.identify import IdentifyScreen
    from openremap.tui.screens.inspect import InspectScreen
    from openremap.tui.screens.maps import MapsScreen

    ansi = re.compile(r"\x1b\[[0-9;]*m")
    builders = [
        (HomeScreen, "#home-status-hints"),
        (lambda: IdentifyScreen(str(M33)), "#identify-next"),
        (lambda: InspectScreen(str(M33)), "#inspect-next"),
        (lambda: MapsScreen(str(M33)), "#maps-hints"),
        (lambda: CookScreen(stock=str(M33)), "#cook-hints"),
        (lambda: ApplyScreen(target=str(M33)), "#apply-hints"),
        (lambda: BatchScreen(str(M33.parent)), "#batch-hints"),
        (ContributeScreen, "#contribute-hints"),
        (HelpScreen, "#help-hints"),
    ]

    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        measured = []
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.05)
            for build, selector in builders:
                screen = build()
                app.push_screen(screen)
                await pilot.pause(0.4)
                bar = screen.query_one(selector)
                text = ansi.sub("", str(bar.render()))
                measured.append(
                    (selector, max((len(line) for line in text.splitlines()), default=0), bar.size.height)
                )
                app.pop_screen()
                await pilot.pause(0.1)
        return measured

    problems = []
    for selector, longest, height in asyncio.run(scenario()):
        if height < 1:
            problems.append(f"{selector}: no row to draw in")
        elif longest > 78:
            problems.append(f"{selector}: {longest} columns")
    assert not problems, "key bars that do not fit 80x24: " + ", ".join(problems)


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
def test_the_batch_bar_has_a_row_to_draw_in():
    from openremap.tui.screens.batch import BatchScreen

    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.05)
            screen = BatchScreen()
            app.push_screen(screen)
            await pilot.pause(0.3)
            bar = screen.query_one("#batch-hints")
            return bar.size.height, str(bar.render())

    height, text = asyncio.run(scenario())
    assert height >= 1
    assert "folder" in text
