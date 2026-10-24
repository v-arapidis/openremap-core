"""
Tests for the INSPECT screen.

Most of these exist because the first inline version *lied*: it rendered a
checksum detector that had failed (``ok: 0, total: 1``) as if the scheme were
verified, and reduced a failed health check to "has findings". The screen must
name what is wrong and must say "unknown" when nothing could be checked.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.inspect import InspectScreen

ROOT = Path(__file__).parent.parent.parent
STALE_DUMP = ROOT / "tests/data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"
GOOD_DUMP = ROOT / "tests/data/ECUs/Siemens/MS43/MS43_WBABW510X0PK46741_430069_512KB.bin"
NO_PROFILE_DUMP = ROOT / "tests/data/ECUs/Bosch/EDC16/038906016L_0281011433_v370476_EDC16U31_2004AT_OEM__1__1.bin"


def run(body, *, size=(110, 40)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


async def _inspect(pilot, app, path: Path, timeout: float = 120.0) -> InspectScreen:
    """Push the screen and wait for the real analysis, however long it takes.

    ``analyze`` needs ~21 s on the 4 MB EDC17 dump, so the wait is a wall-clock
    budget, not a frame count. (It used to be a frame count, which only ever
    passed because the engine froze the event loop for the whole call.)
    """
    screen = InspectScreen(str(path))
    app.push_screen(screen)
    deadline = time.monotonic() + timeout
    while screen.analysis is None and time.monotonic() < deadline:
        await pilot.pause(0.05)
    return screen


def _text(screen: InspectScreen, selector: str) -> str:
    return str(screen.query_one(selector).render())


class TestEmptyState:
    def test_asks_for_a_file(self):
        async def body(pilot, app):
            app.push_screen(InspectScreen())
            await pilot.pause(0.05)
            return _text(app.screen, "#inspect-next")

        assert "Press F" in run(body)


@pytest.mark.skipif(not STALE_DUMP.exists(), reason="test corpus absent")
class TestFailingChecksum:
    def test_stale_scheme_is_named_not_verified(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, STALE_DUMP)
            return _text(screen, "#inspect-checksums")

        text = run(body)
        assert "STALE" in text
        assert "0/1" in text
        assert "verified" not in text.lower()

    def test_health_names_the_failing_check(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, STALE_DUMP)
            return _text(screen, "#inspect-health")

        text = run(body)
        assert "findings" in text.lower()
        assert "checksums" in text


@pytest.mark.skipif(not GOOD_DUMP.exists(), reason="test corpus absent")
class TestPassingChecksum:
    def test_complete_family_reports_ok(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, GOOD_DUMP)
            return _text(screen, "#inspect-checksums")

        text = run(body)
        assert "ms43" in text
        assert "3/3" in text
        assert "OK" in text


TUNE_STOCK = ROOT / "tests/data/tune/original.bin"


class TestNoProfile:
    """A file with nothing to verify must say *unknown*, not "fine"."""

    @pytest.mark.skipif(not TUNE_STOCK.exists(), reason="test corpus absent")
    def test_absence_is_stated_as_unknown(self):
        """EDC17C66: no detector fires at all, so the honest answer is unknown."""

        async def body(pilot, app):
            screen = await _inspect(pilot, app, TUNE_STOCK)
            return _text(screen, "#inspect-checksums")

        text = run(body)
        assert "no scheme verified" in text.lower()
        assert "unknown" in text.lower()

    def test_a_detector_firing_is_never_reported_as_verified(self, tmp_path):
        """A generated dump trips the GS20 detector and fails it — say STALE."""
        import base64

        from openremap.api import call

        dump = call("generate_dump", {"size": 65536, "seed": 11})
        target = tmp_path / "synthetic.bin"
        target.write_bytes(base64.b64decode(dump["data_b64"]))

        async def body(pilot, app):
            screen = await _inspect(pilot, app, target)
            return _text(screen, "#inspect-checksums")

        text = run(body)
        assert "STALE" in text
        assert "verified" not in text.lower()


@pytest.mark.skipif(not NO_PROFILE_DUMP.exists(), reason="test corpus absent")
class TestVerifiedScheme:
    def test_a_passing_scheme_is_ok(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, NO_PROFILE_DUMP)
            return _text(screen, "#inspect-checksums")

        text = run(body)
        assert "crc8" in text
        assert "1/1" in text
        assert "OK" in text


@pytest.mark.skipif(not NO_PROFILE_DUMP.exists(), reason="test corpus absent")
class TestLayout:
    def test_regions_are_listed_with_ranges(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, NO_PROFILE_DUMP)
            return _text(screen, "#inspect-layout")

        text = run(body)
        assert "0x" in text  # a region range
        assert "tables" in text

    def test_coherence_is_read_per_check(self):
        async def body(pilot, app):
            screen = await _inspect(pilot, app, NO_PROFILE_DUMP)
            return _text(screen, "#inspect-coherence")

        text = run(body)
        assert "identity_checksum" in text
        assert "identity_arch" in text
