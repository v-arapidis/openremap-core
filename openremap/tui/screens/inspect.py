"""
The INSPECT screen — is this file sane, and *why*?

IDENTIFY answers *what it is*. This answers *whether it holds together*, using
the full analysis (health, checksums, flash layout, maps, coherence). It exists
so the detail has a home instead of being squeezed into six lines under the
identity block.

The rendering rules exist because the first attempt was wrong:

* **A detector firing is not a scheme verifying.** ``ironfelix`` with
  ``{"ok": 0, "total": 1}`` means STALE, and the screen says so — it never
  reports "verified" unless every check passed.
* **Name the failing check.** ``health.healthy == false`` is rendered as the
  checks that failed, with their messages, not as "has findings".
* **Coherence is read per check** (``agree`` / ``gap`` / ``conflict`` with the
  detector's own sentence), never as the summary verdict alone.
* **Absence is stated as absence**: when no scheme fires, the line says there is
  no profile for that family, which is different from "the checksum is fine".
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from openremap.tui import paths
from openremap.tui.screens.picker import FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["InspectScreen"]

_STATUS = {
    "ok": ("✓", "#4ad6a8"),
    "agree": ("✓", "#4ad6a8"),
    "warn": ("!", "#ffd166"),
    "gap": ("·", "#5a6b85"),
    "skip": ("·", "#3a4557"),
    "fail": ("✗", "#ff6b6b"),
    "conflict": ("✗", "#ff6b6b"),
}


def _glyph(status: str) -> tuple[str, str]:
    return _STATUS.get(status, ("·", "#3a4557"))


def _human_size(count: int) -> str:
    for unit in ("B", "KB", "MB"):
        if count < 1024 or unit == "MB":
            return f"{count:,} B" if unit == "B" else f"{count / 1024 ** ('KB' == unit):,.0f} {unit}"
    return f"{count:,} B"


class InspectScreen(Screen):
    """Health, checksums, layout and coherence for one file."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "pick", "Open file", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("r", "refresh", "Re-run", show=True),
    ]

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = path
        self.analysis: dict[str, Any] | None = None
        self.elapsed_ms = 0.0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="inspect-panel"):
            with Horizontal(id="inspect-file"):
                yield Static("", id="inspect-file-path")
                yield Static("", id="inspect-file-meta")
            yield ActivityBar(id="inspect-activity")
            yield Static("", id="inspect-health")
            yield Static("", id="inspect-checksums")
            yield Static("", id="inspect-coherence")
            yield Static("", id="inspect-layout")
            yield Static("", id="inspect-next")

    def on_mount(self) -> None:
        if self.path:
            self.load(self.path)
        else:
            self._paint_empty()

    def _paint_empty(self) -> None:
        self.query_one("#inspect-file-path", Static).update("no file")
        self.query_one("#inspect-health", Static).update(
            Text("INSPECT\n", style="bold #e8eef7")
        )
        self.query_one("#inspect-next", Static).update(
            Text("  Press F to choose a dump, or Esc to go back.", style="#5a6b85")
        )

    # -- work -----------------------------------------------------------
    def load(self, path: str) -> None:
        self.path = path
        self.analysis = None
        from pathlib import Path

        self.query_one("#inspect-file-path", Static).update(
            Text(paths.label(Path(path)), style="#c8d1e0")
        )
        self.run_worker(self._analyse(path), name="inspect", exclusive=True)

    async def _analyse(self, path: str) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#inspect-activity", ActivityBar)
        activity.start("analysing (health, checksums, layout, maps, coherence)")
        started = time.perf_counter()
        try:
            analysis = await asyncio.to_thread(call, "analyze", {"path": path})
        except Exception as exc:  # noqa: BLE001 - report, never crash
            activity.stop()
            self.query_one("#inspect-health", Static).update(
                Text(f"could not analyse: {type(exc).__name__}: {exc}",
                     style="bold #ff6b6b")
            )
            return

        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.analysis = analysis
        self.app.note_recent(self.path, "dump")
        activity.stop(f"analysed in {self.elapsed_ms:.0f} ms")
        self._paint()

    # -- rendering ------------------------------------------------------
    def _paint(self) -> None:
        data = self.analysis or {}
        size = data.get("file_size") or 0
        digest = (data.get("sha256") or "")[:12]
        self.query_one("#inspect-file-meta", Static).update(
            Text(f"{size:,} B   sha256 {digest}…", style="#5a6b85")
        )
        self._paint_health(data)
        self._paint_checksums(data)
        self._paint_coherence(data)
        self._paint_layout(data)
        self.query_one("#inspect-next", Static).update(
            Text("  [R] re-run   [F] open another file   [Esc] back", style="#3a4557")
        )

    def _paint_health(self, data: dict[str, Any]) -> None:
        health = data.get("health") or {}
        checks = health.get("checks") or []
        block = Text()
        verdict = "healthy" if health.get("healthy") else self._verdict_word(checks)
        block.append("HEALTH  ", style="bold #e8eef7")
        block.append(verdict + "\n", style="#4ad6a8" if health.get("healthy") else "#ffd166")
        for check in checks:
            status = str(check.get("status") or "skip")
            glyph, colour = _glyph(status)
            block.append(f"  {glyph} ", style=colour)
            block.append(f"{check.get('name', '?')!s:<14}", style="#8aa0c0")
            message = check.get("message") or check.get("detail") or ""
            block.append(f"{message}\n", style="#5a6b85")
        self.query_one("#inspect-health", Static).update(block)

    @staticmethod
    def _verdict_word(checks: list[dict[str, Any]]) -> str:
        failed = [c for c in checks if c.get("status") == "fail"]
        if failed:
            return "findings: " + ", ".join(str(c.get("name")) for c in failed)
        warned = [c for c in checks if c.get("status") == "warn"]
        if warned:
            return "warnings: " + ", ".join(str(c.get("name")) for c in warned)
        return "nothing conclusive"

    def _paint_checksums(self, data: dict[str, Any]) -> None:
        sums = data.get("checksums") or {}
        block = Text()
        block.append("CHECKSUMS\n", style="bold #e8eef7")
        rows = 0

        for scheme in sums.get("schemes") or []:
            rate = scheme.get("rate")
            pages = scheme.get("pages") or ""
            verdict = "OK" if (rate is None or rate >= 1.0) else "STALE"
            colour = "#4ad6a8" if verdict == "OK" else "#ff6b6b"
            block.append(f"  {'✓' if verdict == 'OK' else '✗'} ", style=colour)
            block.append(f"{scheme.get('algo', '?')}", style="#c8d1e0")
            block.append(f"  {scheme.get('region', '')}", style="#5a6b85")
            block.append(f"  pages {pages}", style="#3a4557")
            block.append(f"  {verdict}\n", style=f"bold {colour}")
            rows += 1

        for family in ("me7", "denso", "ms43"):
            value = sums.get(family)
            if not value:
                continue
            ok, total = self._pair(value)
            colour = "#4ad6a8" if ok == total else "#ff6b6b"
            verdict = "OK" if ok == total else "STALE"
            block.append(f"  {'✓' if verdict == 'OK' else '✗'} ", style=colour)
            block.append(f"{family}", style="#c8d1e0")
            block.append(f"  {ok}/{total} checks", style="#5a6b85")
            block.append(f"  {verdict}\n", style=f"bold {colour}")
            rows += 1

        for entry in sums.get("ironfelix") or []:
            ok = int(entry.get("ok") or 0)
            total = int(entry.get("total") or 0) or 1
            colour = "#4ad6a8" if ok == total else "#ff6b6b"
            verdict = "OK" if ok == total else "STALE"
            block.append(f"  {'✓' if verdict == 'OK' else '✗'} ", style=colour)
            block.append(f"{entry.get('description', 'ironfelix')}", style="#c8d1e0")
            block.append(f"  {ok}/{total}", style="#5a6b85")
            block.append(f"  {verdict}\n", style=f"bold {colour}")
            rows += 1

        if not rows:
            block.append(
                "  · no scheme verified — no profile for this family "
                "(checksum status unknown, not \u201cfine\u201d)\n",
                style="#5a6b85",
            )
        self.query_one("#inspect-checksums", Static).update(block)

    @staticmethod
    def _pair(value: Any) -> tuple[int, int]:
        if isinstance(value, dict):
            return int(value.get("ok") or 0), int(value.get("total") or 0)
        if value is True:
            return 1, 1
        return 0, 1

    def _paint_coherence(self, data: dict[str, Any]) -> None:
        coherence = data.get("coherence") or {}
        block = Text()
        block.append("COHERENCE", style="bold #e8eef7")
        block.append(f"  {coherence.get('verdict') or coherence.get('status') or '—'}\n",
                     style="#5a6b85")
        for check in coherence.get("checks") or []:
            status = str(check.get("status") or "gap")
            glyph, colour = _glyph(status)
            block.append(f"  {glyph} ", style=colour)
            block.append(f"{check.get('name', '?')!s:<18}", style="#8aa0c0")
            block.append(f"{check.get('detail') or status}\n", style="#5a6b85")
        self.query_one("#inspect-coherence", Static).update(block)

    def _paint_layout(self, data: dict[str, Any]) -> None:
        layout = data.get("layout") or {}
        maps = data.get("maps") or {}
        regions = layout.get("regions") or []
        block = Text()
        block.append("LAYOUT & MAPS\n", style="bold #e8eef7")
        for region in regions:
            start = region.get("start") or 0
            end = region.get("end") or 0
            block.append(f"  {region.get('kind', '?')!s:<12}", style="#8aa0c0")
            block.append(f"0x{start:06X}–0x{end:06X}", style="#5a6b85")
            block.append(f"  {end - start:,} B\n", style="#3a4557")
        block.append(
            f"  {'maps':<12}{maps.get('table_count', 0)} candidate tables · "
            f"{maps.get('axis_count', 0)} axes · "
            f"{len(layout.get('ident_blocks') or [])} ident blocks\n",
            style="#5a6b85",
        )
        vin = data.get("vin")
        if vin:
            block.append(f"  {'VIN':<12}{vin}\n", style="#c8d1e0")
        self.query_one("#inspect-layout", Static).update(block)

    # -- actions --------------------------------------------------------
    def action_pick(self) -> None:
        from pathlib import Path

        start = Path(self.path).parent if self.path else None

        def _chosen(path) -> None:
            if path is not None:
                self.load(str(path))

        self.app.push_screen(FilePicker(start), _chosen)

    def action_refresh(self) -> None:
        if self.path:
            self.load(self.path)

    def action_back(self) -> None:
        self.app.pop_screen()
