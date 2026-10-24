"""
Tests for the TUI boot screen.

The screen is the app's first health check, so the tests pin what it is *for*:

* every step runs and ends ``ok`` on a healthy install,
* the rows are the honest inventory (labels, real details, real timings),
* a failing step does not crash the app — it shows the repair panel with the
  remedy for that step, and ``R`` re-runs the sequence,
* ``--fast`` changes nothing about the work, because there is no artificial
  delay to skip.

Textual's test harness is async and this project has no async pytest plugin, so
each test drives its own event loop. Everything is read *inside* the app context
— querying widgets after it exits raises ``NoMatches``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.boot import BootScreen, render_wordmark

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    finished: bool = False
    failed_key: str | None = None
    remedy: str = ""
    steps: list[tuple[str, str, float]] = field(default_factory=list)
    status: str = ""
    bar: str = ""
    repair: str | None = None


async def _settle(pilot, screen: BootScreen, done) -> None:
    for _ in range(500):
        if done(screen):
            return
        await pilot.pause(0.02)


def _collect(screen: BootScreen) -> Outcome:
    repair = screen.query("#boot-repair")
    return Outcome(
        finished=screen.finished,
        failed_key=getattr(screen.failed_step, "key", None),
        remedy=getattr(screen.failed_step, "remedy", ""),
        steps=[(s.label, s.detail, s.elapsed_ms) for s in screen.steps],
        status=str(screen.query_one("#boot-status").render()),
        bar=str(screen.query_one("#boot-progress").render()),
        repair=str(repair.first().render()) if repair else None,
    )


def boot(*, fast: bool = True, retry: bool = False) -> Outcome:
    """Run the app, wait for the boot to settle, collect, optionally retry."""

    async def scenario() -> Outcome:
        app = OpenRemapTUI()
        app.fast_boot = fast
        async with app.run_test(size=(100, 40)) as pilot:
            screen = app.screen
            await _settle(pilot, screen, lambda s: s.finished or s.failed_step is not None)
            first = _collect(screen)
            if not (retry and first.failed_key):
                return first
            await pilot.press("r")
            await _settle(pilot, screen, lambda s: s.finished or s.failed_step is not None)
            return _collect(screen)

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Wordmark
# ---------------------------------------------------------------------------


class TestWordmark:
    def test_wordmark_is_five_rows_of_blocks(self):
        art = render_wordmark()
        assert len(art.splitlines()) == 5
        assert set(art) <= {"█", " ", "\n"}

    def test_wordmark_is_wide_enough_to_read(self):
        assert len(render_wordmark().splitlines()[0]) >= 40


# ---------------------------------------------------------------------------
# Healthy boot
# ---------------------------------------------------------------------------


class TestBootSequence:
    def test_all_steps_pass(self):
        result = boot()
        assert result.finished is True
        assert result.failed_key is None
        assert [label for label, _, _ in result.steps] == [
            "framework",
            "engine",
            "services",
            "extractors",
            "workspace",
        ]

    def test_every_step_reports_a_real_detail_and_timing(self):
        result = boot()
        for label, detail, elapsed in result.steps:
            assert detail, label
            assert elapsed >= 0.0

    def test_ready_status_mentions_steps_and_exit(self):
        result = boot()
        assert "READY" in result.status
        assert "5" in result.status
        assert "[Q]" in result.status

    def test_progress_bar_is_full(self):
        result = boot()
        assert "░" not in result.bar
        assert "▓" in result.bar


# ---------------------------------------------------------------------------
# Failure + retry
# ---------------------------------------------------------------------------


def _break_engine(monkeypatch) -> None:
    """Make the engine step fail, by replacing the step definitions themselves."""
    import openremap.tui.screens.boot as boot_mod

    def boom() -> str:
        raise RuntimeError("rust extension missing")

    broken = tuple(
        boot_mod.BootStep(step.key, step.label, boom if step.key == "engine" else step.run)
        for step in boot_mod.BOOT_STEPS
    )
    monkeypatch.setattr(boot_mod, "BOOT_STEPS", broken)


class TestFailurePath:
    def test_failing_step_shows_repair_panel(self, monkeypatch):
        _break_engine(monkeypatch)
        result = boot()
        assert result.finished is False
        assert result.failed_key == "engine"
        assert "maturin" in result.remedy
        assert result.repair is not None
        assert "engine" in result.repair
        assert "[R]" in result.repair
        assert "[Q]" in result.repair

    def test_retry_reruns_the_sequence(self, monkeypatch):
        import openremap.tui.screens.boot as boot_mod

        calls = {"n": 0}
        original_engine_step = next(s.run for s in boot_mod.BOOT_STEPS if s.key == "engine")

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return original_engine_step()

        broken = tuple(
            boot_mod.BootStep(step.key, step.label, flaky if step.key == "engine" else step.run)
            for step in boot_mod.BOOT_STEPS
        )
        monkeypatch.setattr(boot_mod, "BOOT_STEPS", broken)

        result = boot(retry=True)
        assert calls["n"] >= 2
        assert result.finished is True
        assert result.failed_key is None
