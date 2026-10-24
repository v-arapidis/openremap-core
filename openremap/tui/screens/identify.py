"""
The IDENTIFY screen — "what is this file?"

Two tiers, mirroring the CLI:

* **identify** — fast (70 ms for 256 KB, ~1 s for 4 MB): who made it, which
  family, which software, which numbers, and how strongly that was established.
* **analyse** (F2) — the heavy picture: health, checksums, flash layout, maps,
  coherence.  It runs the extra passes and reports counts plus the first
  problems.

Honesty rules the screen follows:

* The headline is ``detection_strength``, not an invented percentage. ``strong``
  means the extractor validated its own signatures *against this file*.
* Fields the family does not expose are hidden; a whole missing group becomes
  one dim sentence instead of a wall of dashes.
* Unrecognised files say **UNKNOWN** and offer the honest next steps, including
  contributing an extractor.
* Nothing is written to disk here. The only writing action (cook) shows its
  output name before it runs — and it does not exist yet.
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

from openremap.tui.screens.picker import FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["IdentifyScreen"]

#: Identity fields, in display order, with the label used in the screen.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("software_version", "software"),
    ("hardware_number", "hardware"),
    ("oem_part_number", "OEM part"),
    ("calibration_id", "calibration"),
    ("calibration_version", "calibration ver"),
    ("sw_base_version", "SW base"),
    ("serial_number", "serial"),
    ("dataset_number", "dataset"),
    ("match_key", "match key"),
)

#: Reported per family, but only worth a footnote.
_TRAITS: tuple[tuple[str, str], ...] = (
    ("ecu_endian", "endian"),
    ("ecu_cell_bytes", "cells"),
    ("container", "container"),
)

_STRENGTH_WORDS = {
    "strong": ("STRONG", "#4ad6a8"),
    "medium": ("MEDIUM", "#ffd166"),
    "weak": ("WEAK", "#ffb454"),
    None: ("UNCLAIMED", "#ff6b6b"),
}


class IdentifyScreen(Screen):
    """Identify one binary, then offer the next step."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "pick", "Open file", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("i", "inspect", "Inspect", show=True),
        Binding("m", "maps", "Maps", show=True),
        Binding("c", "cook", "Cook", show=True),
    ]

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = path
        self.identity: dict[str, Any] | None = None
        self.elapsed_ms: float = 0.0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="identify-panel"):
            with Horizontal(id="identify-file"):
                yield Static("", id="identify-file-path")
                yield Static("", id="identify-file-meta")
            yield Static("", id="identify-headline")
            yield ActivityBar(id="identify-activity")
            yield Static("", id="identify-body")
            yield Static("", id="identify-next")

    def on_mount(self) -> None:
        if self.path:
            self.load(self.path)
        else:
            self._paint_empty()

    # -- states ---------------------------------------------------------
    def _paint_empty(self) -> None:
        self.query_one("#identify-file-path", Static).update("no file")
        self.query_one("#identify-headline", Static).update(
            Text("IDENTIFY", style="bold #e8eef7")
        )
        self.query_one("#identify-body", Static).update(
            Text("Press F to choose a dump, or Esc to go back.", style="#5a6b85")
        )
        self.query_one("#identify-next", Static).update(
            Text("", style="#3a4557")
        )

    def _set_busy(self, message: str) -> None:
        self.query_one("#identify-activity", ActivityBar).start(message)

    # -- the work -------------------------------------------------------
    def load(self, path: str) -> None:
        """Identify *path* in a worker so the screen stays responsive."""
        self.path = path
        self.identity = None
        self._paint_file()
        self._set_busy("identifying")
        self.run_worker(self._identify(path), name="identify", exclusive=True)

    def _paint_file(self) -> None:
        from pathlib import Path

        target = Path(self.path or "")
        from openremap.tui import paths as _paths

        self.query_one("#identify-file-path", Static).update(
            Text(_paths.label(target), style="#c8d1e0")
        )

    async def _identify(self, path: str) -> None:
        import asyncio

        from openremap.api import call

        started = time.perf_counter()
        try:
            identity = await asyncio.to_thread(call, "identify", {"path": path})
        except Exception as exc:  # noqa: BLE001 - report, never crash the screen
            self.query_one("#identify-activity", ActivityBar).stop()
            self.query_one("#identify-headline", Static).update(
                Text(f"could not read the file: {type(exc).__name__}: {exc}",
                     style="bold #ff6b6b")
            )
            return

        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.identity = identity
        self.app.note_recent(self.path, "dump")
        self.query_one("#identify-activity", ActivityBar).stop(
            f"identified in {self.elapsed_ms:.0f} ms"
        )
        self._paint_identity()
        self._paint_next()

    # -- rendering ------------------------------------------------------
    def _paint_identity(self) -> None:
        data = self.identity or {}
        family = data.get("ecu_family")
        variant = data.get("ecu_variant")
        maker = data.get("manufacturer")
        strength = data.get("detection_strength")
        word, colour = _STRENGTH_WORDS.get(strength, _STRENGTH_WORDS[None])

        headline = Text()
        if family:
            parts = [p for p in (maker, family, variant) if p]
            headline.append("  ·  ".join(parts), style="bold #e8eef7")
        else:
            headline.append("UNKNOWN", style="bold #ff6b6b")
            headline.append("   no extractor recognised this file", style="#5a6b85")
        if family:
            headline.append(f"        detection {word}", style=f"bold {colour}")
        headline.append(f"        {self.elapsed_ms:.0f} ms", style="#3a4557")
        self.query_one("#identify-headline", Static).update(headline)

        size = data.get("file_size") or 0
        digest = (data.get("sha256") or "")[:12]
        self.query_one("#identify-file-meta", Static).update(
            Text(f"{size:,} B   sha256 {digest}…", style="#5a6b85")
        )

        body = Text()
        shown = 0
        for field, label in _FIELDS:
            value = data.get(field)
            if not value:
                continue
            body.append(f"  {label:<16}", style="#5a6b85")
            body.append(f"{value}\n", style="#c8d1e0")
            shown += 1

        if not shown:
            body.append(
                "  no identity strings were found in this dump\n", style="#3a4557"
            )

        traits = [f"{label} {data[field]}" for field, label in _TRAITS if data.get(field)]
        if traits:
            body.append("  " + "   ·   ".join(traits) + "\n", style="#3a4557")
        self.query_one("#identify-body", Static).update(body)

    def _paint_next(self) -> None:
        known = bool((self.identity or {}).get("ecu_family"))
        next_line = Text("  ", style="#3a4557")
        hints = [
            ("I", "inspect"),
            ("M", "maps"),
            ("C", "cook"),
        ]
        # Nothing claims an unknown file, so the useful next step is a second
        # look — not a menu of screens that do not exist yet.
        hints.append(("F", "open another dump") if known else ("F", "try another"))
        for key, label in hints:
            next_line.append(f"[{key}] ", style="bold #4ad6a8")
            next_line.append(f"{label}    ", style="#8aa0c0")
        self.query_one("#identify-next", Static).update(next_line)

    # -- actions --------------------------------------------------------
    def action_pick(self) -> None:
        """Pick a file, starting next to the current one when there is one."""
        from pathlib import Path

        start = Path(self.path).parent if self.path else None

        def _chosen(path) -> None:
            if path is not None:
                self.load(str(path))

        self.app.push_screen(FilePicker(start), _chosen)

    def action_cook(self) -> None:
        """Start COOK with this dump as the stock file.

        The file you were just looking at is almost always the "before" side, so
        it travels with you — that avoids a second trip through the picker.
        """
        if not self.path:
            self.app.notify("choose a file first — press F", timeout=4)
            return
        from openremap.tui.screens.cook import CookScreen

        self.app.push_screen(CookScreen(stock=self.path))

    def action_maps(self) -> None:
        """Open MAPS on this dump — table scanning is structural, so it needs no
        identity and works for unclaimed files too."""
        from openremap.tui.screens.maps import MapsScreen

        self.app.push_screen(MapsScreen(self.path))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_inspect(self) -> None:
        """Open INSPECT: health, checksums, layout, coherence for this file.

        There is no separate "analyse" screen — ``analyze`` is the command
        behind INSPECT, so the key and the screen are one and the same thing.
        """
        if not self.path:
            self.app.notify("choose a file first — press F", timeout=4)
            return
        from openremap.tui.screens.inspect import InspectScreen

        self.app.push_screen(InspectScreen(self.path))

