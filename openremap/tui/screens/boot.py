"""
The boot screen — what you see when the TUI starts.

It is not a splash that pretends to work. Every line is **real startup work**
with its real duration, so the screen doubles as the first health check: if the
Rust extension is missing or the workspace cannot be created, you learn it here,
in plain words, instead of hitting a strange failure three screens later.

Sequence
--------
framework  -> textual + rich are importable and what version they are
engine     -> the mandatory Rust extension imports and exposes its entry points
services   -> the API gate loads and how many methods / workflows it offers
extractors -> the manufacturer registry loads and how many extractors it holds
workspace  -> ~/OpenRemap/{ecus,recipes,tunes} exist (created if missing)

A failing step turns red and opens a repair panel with the command that fixes it
(``R`` retries the whole sequence, ``Q`` quits). Nothing is skipped silently.

There is **no artificial delay**: the screen is done when the work is done. When
a home screen exists, the finished screen will hold for ``MIN_VISIBLE_SECONDS``
before advancing so it stays legible on a fast machine; ``--fast`` /
``OPENREMAP_TUI_FAST=1`` will skip that hold.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from openremap import __version__

__all__ = ["BOOT_STEPS", "BootScreen"]

#: Reserved for the moment a screen follows this one: how long the *finished*
#: boot screen holds before advancing. It would hold a finished screen — it can
#: never fake progress.
MIN_VISIBLE_SECONDS = 0.6

_WORDMARK_FONT: dict[str, tuple[str, ...]] = {
    "O": (" ████ ", "█    █", "█    █", "█    █", " ████ "),
    "P": ("████ ", "█   █", "████ ", "█    ", "█    "),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
    "N": ("█   █", "██  █", "█ █ █", "█  ██", "█   █"),
    "R": ("████ ", "█   █", "████ ", "█ █  ", "█  █ "),
    "M": ("█   █", "██ ██", "█ █ █", "█   █", "█   █"),
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    " ": ("  ", "  ", "  ", "  ", "  "),
}


def render_wordmark(text: str = "OPENREMAP") -> str:
    """Draw *text* as a five-row block wordmark."""
    rows = []
    for row in range(5):
        rows.append(" ".join(_WORDMARK_FONT.get(ch, _WORDMARK_FONT[" "])[row] for ch in text))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# The real work
# ---------------------------------------------------------------------------


def _step_framework() -> str:
    import importlib.metadata as md

    return f"textual {md.version('textual')} · rich {md.version('rich')}"


def _step_engine() -> str:
    from openremap._rust import (  # noqa: F401
        checksum_compute,
        scan_map_axes,
        scan_map_tables,
    )

    return "openremap._rust · maps · checksums"


def _step_services() -> str:
    import openremap.api.commands
    import openremap.api.composite
    import openremap.api.workflows  # noqa: F401
    from openremap.api.registry import _REGISTRY

    workflows = sum(1 for meta in _REGISTRY.values() if meta["kind"] == "workflow")
    return f"{len(_REGISTRY)} methods · {workflows} workflow"


def _step_extractors() -> str:
    from openremap.core.manufacturers import get_extractors

    extractors = get_extractors()
    brands = sorted({e.name for e in extractors})
    return f"{len(extractors)} extractors · {len(brands)} manufacturers"


def _step_workspace() -> str:
    """Ensure the workspace folders exist; never fatal.

    A read-only home is a real situation, and it does not stop identification or
    analysis — only saving. The step therefore *reports* the problem instead of
    failing the boot.
    """
    from openremap.tui import paths

    base = paths.workspace()
    label = paths.label(base)
    if not os.access(base, os.W_OK):
        return f"{label} · read-only — analysis only"

    created = []
    try:
        for name in ("ecus", "recipes", "tunes"):
            folder = base / name
            if not folder.is_dir():
                folder.mkdir(parents=True, exist_ok=True)
                created.append(name)
    except OSError:
        return f"{label} · read-only — analysis only"
    return f"{label}" + (f" · created {', '.join(created)}" if created else " · ready")


@dataclass
class BootStep:
    key: str
    label: str
    run: Callable[[], str]
    detail: str = ""
    state: str = "pending"  # pending | running | ok | failed
    elapsed_ms: float = 0.0

    @property
    def remedy(self) -> str:
        return {
            "framework": "Textual did not load — run:  uv sync",
            "engine": "the Rust extension is missing — run:  uv run --with maturin "
            "maturin develop --release",
            "services": "the API registry did not load — check the install:  "
            "openremap --version",
            "extractors": "the extractor registry did not load — check the install:  "
            "openremap --version",
            "workspace": "cannot create ~/OpenRemap — check your home directory "
            "permissions",
        }.get(self.key, "unexpected failure")


BOOT_STEPS: tuple[BootStep, ...] = (
    BootStep("framework", "framework", _step_framework),
    BootStep("engine", "engine", _step_engine),
    BootStep("services", "services", _step_services),
    BootStep("extractors", "extractors", _step_extractors),
    BootStep("workspace", "workspace", _step_workspace),
)


class StepRow(Horizontal):
    """One boot step: glyph+label, what it found, how long it took.

    Three widgets in a ``Horizontal`` rather than one padded f-string, so the
    timing column is genuinely right-aligned whatever the terminal width — a
    fixed-width string was clipping the timings off the end of the row.
    """

    def __init__(self, step: BootStep) -> None:
        super().__init__(id=f"step-{step.key}", classes="boot-step pending")
        self.step = step

    def compose(self) -> ComposeResult:
        yield Static("", classes="step-label")
        yield Static("", classes="step-detail")
        yield Static("", classes="step-timing")

    def refresh_row(self) -> None:
        step = self.step
        glyph = {"pending": "·", "running": "…", "ok": "✓", "failed": "✗"}[step.state]
        self.query_one(".step-label", Static).update(f"{glyph}  {step.label}")
        self.query_one(".step-detail", Static).update(step.detail)
        self.query_one(".step-timing", Static).update(
            f"{step.elapsed_ms:.0f} ms" if step.state in ("ok", "failed") else ""
        )
        self.set_classes(f"boot-step {step.state}")


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


class BootScreen(Screen):
    """Power-on self-test: real steps, real timings, no fake progress."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "retry", "Retry", show=False),
        Binding("enter", "continue", "Continue", show=False),
    ]

    def __init__(self, fast: bool = False) -> None:
        super().__init__()
        self.fast = fast or os.environ.get("OPENREMAP_TUI_FAST") == "1"
        self.steps = [
            BootStep(s.key, s.label, s.run) for s in BOOT_STEPS
        ]
        self.finished = False
        self.failed_step: BootStep | None = None
        self._rows: dict[str, StepRow] = {}
        self._started = time.perf_counter()

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Center(id="boot-center"), Vertical(id="boot-panel"):
            yield Static(render_wordmark(), id="boot-wordmark", markup=False)
            yield Static(
                f"ECU toolkit   ·   v{__version__}",
                id="boot-tagline",
            )
            with Vertical(id="boot-steps"):
                for step in self.steps:
                    row = StepRow(step)
                    self._rows[step.key] = row
                    yield row
            yield Static(self._bar(0), id="boot-progress")
            yield Static("", id="boot-status", markup=False)

    def on_mount(self) -> None:
        for step in self.steps:
            self._paint(step)
        self._set_status("starting…", tone="dim")
        self.run_worker(self._run_steps(), name="boot", exclusive=True)

    # -- painting -------------------------------------------------------
    BAR_WIDTH = 82

    def _bar(self, done: int) -> str:
        """A one-line bar that mirrors how many steps have actually finished."""
        full = round(self.BAR_WIDTH * done / len(self.steps))
        return "▓" * full + "░" * (self.BAR_WIDTH - full)

    def _paint(self, step: BootStep) -> None:
        self._rows[step.key].refresh_row()

    def _set_status(self, text: str, tone: str = "normal") -> None:
        status = self.query_one("#boot-status", Static)
        status.update(text)
        status.set_classes(f"boot-status {tone}")

    # -- the work -------------------------------------------------------
    async def _run_steps(self) -> None:
        bar = self.query_one("#boot-progress", Static)
        for step in self.steps:
            step.state = "running"
            self._paint(step)
            self._set_status(f"{step.label}…", tone="dim")

            started = time.perf_counter()
            try:
                step.detail = await asyncio.to_thread(step.run)
            except Exception as exc:  # noqa: BLE001 - any failure is a boot failure
                step.elapsed_ms = (time.perf_counter() - started) * 1000
                step.state = "failed"
                step.detail = type(exc).__name__
                self._paint(step)
                self._fail(step, exc)
                return

            step.elapsed_ms = (time.perf_counter() - started) * 1000
            step.state = "ok"
            self._paint(step)
            bar.update(self._bar(sum(1 for s in self.steps if s.state == "ok")))

        await self._finish()

    async def _finish(self) -> None:
        # The finished screen holds briefly so a human can read it; ``--fast``
        # (and the test suite) skips the hold, never the checks themselves.
        held = time.perf_counter() - self._started
        if not self.fast and held < MIN_VISIBLE_SECONDS:
            await asyncio.sleep(MIN_VISIBLE_SECONDS - held)
        total_ms = (time.perf_counter() - self._started) * 1000
        self.finished = True
        self._set_status(
            f"READY   ·   {len(self.steps)} steps   ·   {total_ms:.0f} ms"
            "        [Enter] continue   [Q] quit",
            tone="ready",
        )

    def _fail(self, step: BootStep, exc: Exception) -> None:
        self.failed_step = step
        panel = Static(
            f"\n  ✗  {step.label} failed: {type(exc).__name__}: {exc}\n"
            f"\n  → {step.remedy}\n"
            "\n  [R] retry        [Q] quit\n",
            id="boot-repair",
            markup=False,
        )
        self.mount(panel, after=self.query_one("#boot-progress"))
        self._set_status("boot failed", tone="failed")

    # -- actions --------------------------------------------------------
    def action_retry(self) -> None:
        if self.failed_step is None:
            return
        repair = self.query("#boot-repair")
        for widget in repair:
            widget.remove()
        self.failed_step = None
        self.finished = False
        self._started = time.perf_counter()
        self.query_one("#boot-progress", Static).update(self._bar(0))
        for step in self.steps:
            step.state = "pending"
            step.detail = ""
            step.elapsed_ms = 0.0
            self._paint(step)
        self.run_worker(self._run_steps(), name="boot", exclusive=True)

    def action_continue(self) -> None:
        if not self.finished:
            return
        from openremap.tui.screens.home import HomeScreen

        self.app.switch_screen(HomeScreen())
