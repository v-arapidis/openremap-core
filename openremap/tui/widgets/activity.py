"""
The busy line: what is running, and for how long.

Every real action here (identify, analyse, and later cook/apply) blocks for a
measurable time — from ~50 ms to a couple of seconds on a 4 MB dump. Without a
indicator the app looks frozen; with a fake progress bar it would lie.

So this shows two honest things and nothing else: **a spinner** (it is working)
and **elapsed seconds** (how long so far). No percentage, because the services
do not report progress and inventing one would be a lie with a nice font.

A short ``delay`` keeps fast operations from flashing a spinner for one frame.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

__all__ = ["ActivityBar"]

#: Braille spinner — one cell wide, so the line never shifts as it animates.
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class ActivityBar(Static):
    """A one-line activity indicator with a real elapsed-time counter."""

    def __init__(self, *, delay: float = 0.15, **kwargs) -> None:
        super().__init__("", markup=False, **kwargs)
        self.delay = delay
        self.label = ""
        self._started = 0.0
        self._frame = 0
        self._revealed = False
        self._reveal_timer = None
        self._tick_timer = None

    # -- lifecycle ------------------------------------------------------
    def start(self, label: str) -> None:
        """Begin showing *label*. Reveals after ``delay`` to avoid flicker."""
        self.stop_timers()
        self.label = label
        self._started = time.perf_counter()
        self._frame = 0
        self._revealed = False
        self.set_class(True, "running")

        if self.delay <= 0:
            self._reveal()
        else:
            self._reveal_timer = self.set_timer(self.delay, self._reveal)

    def _reveal(self) -> None:
        self._revealed = True
        self._paint()
        self._tick_timer = self.set_interval(0.1, self._paint)

    def _paint(self) -> None:
        if not self._revealed:
            return
        elapsed = time.perf_counter() - self._started
        frame = _FRAMES[self._frame % len(_FRAMES)]
        self._frame += 1
        line = Text()
        line.append(f" {frame} ", style="#4ad6a8")
        line.append(f"{self.label}…", style="#8aa0c0")
        line.append(f"  {elapsed:0.1f}s", style="#3a4557")
        self.update(line)

    def stop(self, text: str = "") -> None:
        """Stop the animation and leave *text* in place (usually the timing)."""
        self.stop_timers()
        self.set_class(False, "running")
        self._revealed = False
        self.update(Text("  " + text, style="#3a4557") if text else "")

    def stop_timers(self) -> None:
        for timer in (self._reveal_timer, self._tick_timer):
            if timer is not None:
                timer.stop()
        self._reveal_timer = None
        self._tick_timer = None

    # -- inspection (used by tests) -------------------------------------
    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._started if self._started else 0.0

    @property
    def running(self) -> bool:
        return self._revealed
