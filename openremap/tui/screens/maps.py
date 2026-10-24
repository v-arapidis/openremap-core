"""
The MAPS screen — find the calibration tables in a dump, and read one.

What this screen is for: **choosing** a table out of a scan. So it is a list, at
full width, with the numbers that make a choice possible — offset, shape, cell
width, score, and the scan's (heuristic) label — plus one line of measured facts
and one line of shape for the highlighted table.

What it is *not* for: drawing the map. A terminal has no pixels, and a map drawn
in characters depends on the font, the colour depth and the aspect ratio of
whatever terminal you happen to be in — it can look wrong while claiming to be
data. So the picture is a file: ``P`` writes an SVG next to the dump (cells,
breakpoints, colour scale), and it is the same picture
``openremap map-image`` writes. The *interactive* 2D/3D view belongs to the GUI,
where there is a GPU to draw it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static

from openremap.tui import paths
from openremap.tui.screens.picker import FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["MapsScreen", "sparkline"]

#: Score thresholds the `S` key cycles through.
_SCORES = (0.85, 0.70, 0.55, 0.0)

#: Sparkline levels, low to high — a shape that needs no colour support.
_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    """One character per value, scaled to the range — a shape, not a plot."""
    if not values:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1
    return "".join(
        _SPARK[min(len(_SPARK) - 1, max(0, round((value - low) / span * (len(_SPARK) - 1))))]
        for value in values
    )


class MapsScreen(Screen):
    """Scan one dump for tables, read one, export it."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "pick", "Open file", show=True),
        Binding("s", "cycle_score", "Score filter", show=True),
        Binding("p", "save_picture", "Save picture", show=True),
        Binding("x", "export_csv", "Export CSV", show=True),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = path
        self.min_score = _SCORES[0]
        self.tables: list[dict[str, Any]] = []
        self.tables_total = 0
        self.layout_filtered = False
        self.selected: dict[str, Any] | None = None
        self.scan_ms = 0.0
        self._grid: list[list[int]] = []
        self._summary: dict[str, Any] = {}

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="maps-panel"):
            with Horizontal(id="maps-head"):
                yield Static("", id="maps-file")
                yield Static("", id="maps-count")
            yield ActivityBar(id="maps-activity")
            yield DataTable(id="maps-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="maps-detail")
            yield Static("", id="maps-profile", markup=False)
            yield Static("", id="maps-hints")

    def on_mount(self) -> None:
        table = self.query_one("#maps-table", DataTable)
        table.add_columns("#", "offset", "shape", "cell", "score", "label")
        if self.path:
            self.scan(self.path)
        else:
            self._paint_empty()

    def _paint_empty(self) -> None:
        self.query_one("#maps-file", Static).update(
            Text("MAPS — press F to choose a dump", style="#5a6b85")
        )
        self.query_one("#maps-hints", Static).update(
            Text("  [F] open   [Esc] back", style="#3a4557")
        )

    def _paint_hints(self) -> None:
        """Advertise exactly the keys that work right now."""
        hints = "  [F] open   [S] score   [Esc] back"
        if self.tables:
            hints += "   [P] picture   [X] CSV"
        self.query_one("#maps-hints", Static).update(Text(hints, style="#3a4557"))

    # -- work -----------------------------------------------------------
    def scan(self, path: str) -> None:
        self.path = path
        self.tables = []
        self.tables_total = 0
        self.layout_filtered = False
        self.selected = None
        self._grid = []
        self._summary = {}
        self.query_one("#maps-file", Static).update(Text(paths.label(Path(path)), style="#c8d1e0"))
        # Paint the state *before* the scan starts: on a 4 MB dump the scan
        # takes seconds, and an empty screen for seconds reads as a hang.
        size = Path(path).stat().st_size if Path(path).exists() else 0
        self.query_one("#maps-count", Static).update(
            Text(f"{size / 1048576:.1f} MB · min score {self.min_score:.2f}", style="#5a6b85")
        )
        self._paint_hints()
        self.app.note_recent(path, "dump")
        self.run_worker(self._scan(path), name="maps-scan", exclusive=True)

    async def _scan(self, path: str) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#maps-activity", ActivityBar)
        size = Path(path).stat().st_size if Path(path).exists() else 0
        activity.start(f"scanning {size / 1048576:.1f} MB for calibration tables")
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                "scan_maps",
                {"path": path, "min_score": self.min_score, "top": 200},
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash
            activity.stop()
            self.query_one("#maps-count", Static).update(
                Text(f"scan failed: {type(exc).__name__}: {exc}", style="bold #ff6b6b")
            )
            return

        self.scan_ms = (time.perf_counter() - started) * 1000
        self.tables = list(result.get("tables") or [])
        self.tables_total = int(result.get("tables_count") or len(self.tables))
        self.layout_filtered = bool(result.get("layout_filtered"))
        activity.stop(f"scanned in {self.scan_ms:.0f} ms")
        self._paint_tables()

    # -- painting -------------------------------------------------------
    def _paint_tables(self) -> None:
        table = self.query_one("#maps-table", DataTable)
        table.clear()
        for index, entry in enumerate(self.tables, 1):
            label = entry.get("label") or "unknown"
            confidence = float(entry.get("label_confidence") or 0.0)
            label_text = f"{label}" + (f" ({confidence:.2f})" if confidence else "")
            table.add_row(
                str(index),
                f"0x{int(entry.get('offset') or 0):06X}",
                f"{entry.get('cols')}×{entry.get('rows')}",
                f"{entry.get('cell_width')} B {str(entry.get('byte_order'))[:1]}",
                f"{float(entry.get('score') or 0):.2f}",
                label_text,
            )
        count = Text()
        count.append(f"{self.tables_total} tables", style="#8aa0c0")
        if len(self.tables) < self.tables_total:
            count.append(f" · {len(self.tables)} shown", style="#5a6b85")
        count.append(f" · min score {self.min_score:.2f}", style="#5a6b85")
        if self.layout_filtered:
            count.append(" · layout regions only", style="#5a6b85")
        self.query_one("#maps-count", Static).update(count)
        self._paint_hints()
        if self.tables:
            table.move_cursor(row=0)
            self._select(0)
        else:
            self._paint_detail_empty()

    def _paint_detail_empty(self) -> None:
        self.query_one("#maps-detail", Static).update(
            Text(f"no tables at score ≥ {self.min_score:.2f}", style="#5a6b85")
        )
        self.query_one("#maps-profile", Static).update(
            Text(
                "the scanner found nothing above this bar. "
                "[S] steps the filter down (0.85 → 0.70 → 0.55 → 0.00).",
                style="#3a4557",
            )
        )

    def _paint_detail(self) -> None:
        """One line of measured facts, one line of shape, for the selection."""
        entry = self.selected
        if not entry:
            self._paint_detail_empty()
            return
        summary = self._summary or {}
        detail = Text()
        detail.append(f"0x{int(entry.get('offset') or 0):06X}", style="#4ad6a8")
        detail.append(
            f"  {entry.get('cols')}×{entry.get('rows')} cells  "
            f"{entry.get('cell_width')} B  score {float(entry.get('score') or 0):.2f}",
            style="#8aa0c0",
        )
        if summary:
            detail.append(
                f"   min {summary.get('min')}  max {summary.get('max')}  "
                f"mean {float(summary.get('mean') or 0):.1f}  "
                f"distinct {summary.get('distinct')}",
                style="#5a6b85",
            )
        self.query_one("#maps-detail", Static).update(detail)

        profile = Text()
        means = [sum(row) / len(row) for row in self._grid if row]
        if means:
            profile.append("  row means  ", style="#3a4557")
            profile.append(sparkline(means), style="#4ad6a8")
            profile.append(
                f"   {min(means):.0f} → {max(means):.0f}"
                f"   ·  [P] writes the picture (SVG)",
                style="#3a4557",
            )
        self.query_one("#maps-profile", Static).update(profile)

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self.tables)):
            return
        self.selected = self.tables[index]
        self._grid = []
        self._summary = {}
        self._paint_detail()
        self.run_worker(self._preview(self.selected), name="maps-preview", exclusive=True)

    async def _preview(self, entry: dict[str, Any]) -> None:
        """Read the cells of the highlighted table — for numbers, not for drawing."""
        import asyncio

        from openremap.api import call

        try:
            result = await asyncio.to_thread(
                call, "map_grid", {"path": self.path, "table": entry}
            )
        except Exception as exc:  # noqa: BLE001
            self.query_one("#maps-detail", Static).update(
                Text(f"cannot read the cells: {type(exc).__name__}", style="#ff6b6b")
            )
            return
        if entry is not self.selected:
            return  # the cursor moved on while this was in flight
        self._grid = list(result.get("rows") or [])
        self._summary = dict(result.get("summary") or {})
        self._paint_detail()

    # -- actions --------------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._select(event.cursor_row)

    def action_pick(self) -> None:
        start = Path(self.path).parent if self.path else None

        def _chosen(path) -> None:
            if path is not None:
                self.scan(str(path))

        self.app.push_screen(FilePicker(start), _chosen)

    def action_cycle_score(self) -> None:
        """Step the score filter — the scanner's own threshold, not ours."""
        current = _SCORES.index(self.min_score) if self.min_score in _SCORES else 0
        self.min_score = _SCORES[(current + 1) % len(_SCORES)]
        if self.path:
            self.scan(self.path)

    def _beside(self, suffix: str) -> Path:
        offset = int((self.selected or {}).get("offset") or 0)
        return Path(self.path).with_name(f"{Path(self.path).stem}_map_0x{offset:06X}{suffix}")

    def action_save_picture(self) -> None:
        """Write the selected table as an SVG picture next to the dump."""
        if not self.selected or not self.path:
            self.app.notify("nothing selected to draw", timeout=3)
            return
        from openremap.api import call

        target = self._beside(".svg")
        try:
            picture = call(
                "map_image",
                {
                    "path": self.path,
                    "table": self.selected,
                    "name": Path(self.path).name,
                },
            )
            target.write_text(picture["svg"], encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"picture failed: {type(exc).__name__}: {exc}", timeout=5)
            return
        self.app.notify(f"picture written: {target.name}", timeout=4)

    def action_export_csv(self) -> None:
        """Write the selected table's cells as CSV next to the dump."""
        if not self.selected or not self.path:
            self.app.notify("nothing selected to export", timeout=3)
            return
        if not self._grid:
            self.app.notify("cells not read yet", timeout=3)
            return
        import csv as _csv

        target = self._beside(".csv")
        try:
            with target.open("w", newline="", encoding="utf-8") as handle:
                _csv.writer(handle).writerows(self._grid)
        except OSError as exc:
            self.app.notify(f"could not write {target.name}: {exc}", timeout=5)
            return
        self.app.notify(f"CSV written: {target.name}", timeout=4)

    def action_back(self) -> None:
        self.app.pop_screen()
