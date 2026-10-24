"""
The DIFF screen — what changed between two dumps, table by table.

The same two files COOK takes, asked a different question: not "make this a
portable recipe" but "what actually moved?". So it is its own screen with its own
keys, and the pair is shared with COOK in both directions (`[D]` there, `[C]`
here) instead of asking the user to pick the same two files twice.

Three things keep it honest, and they are the reason the screen exists at all:

* **What a table explains is shown next to what it does not.** 34 tables changed
  in the reference pair, and 69 changed byte regions belong to no table at all.
  Hiding the second number would make the first one look like the whole story.
* **Nothing is named.** A table is an offset, a shape and a measured axis range.
  No row ever claims to be a torque limiter, because nothing here knows that.
* **The flags change the meaning of a row.** ``realigned`` means the grid was
  found displaced in the tuned file; ``no axis`` means the table was inferred
  from the changed bytes, so its percentages are not relative to stored
  breakpoints — which is why a row can read ``-100%/+100%`` and be ordinary.

Every advertised key exists in the state it is advertised in.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from openremap.tui import paths
from openremap.tui.screens.picker import FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["DiffScreen"]

#: Filter cycle, in order. Each label says what it keeps, not what it hides.
FILTERS: tuple[str, ...] = ("changed", "realigned", "no axis", "all")

FILTER_LABEL = {
    "changed": "every table with a moved cell",
    "realigned": "tables whose grid was found displaced",
    "no axis": "tables inferred from the changed bytes",
    "all": "every matched table, changed or not",
}

#: Rows drawn at a time. A window, not a page: the cursor moves it.
WINDOW = 11

#: Bytes shown per hex window in the unexplained view.
HEX_WINDOW = 96


def _short(name: str, limit: int = 38) -> str:
    """Middle-out shortening, so a long dump name still fits one line."""
    if len(name) <= limit:
        return name
    keep = limit - 1
    head = keep // 2
    return f"{name[:head]}…{name[-(keep - head):]}"


def _size_label(size: int) -> str:
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"


def _table_of(match: dict[str, Any], *, offset: int | None = None) -> dict[str, Any]:
    """The table descriptor ``map_grid`` / ``map_image`` expect, from a diff row."""
    table: dict[str, Any] = {
        "offset": int(match["offset_stock"] if offset is None else offset),
        "cols": int(match["cols"]),
        "rows": int(match["rows"]),
        "cell_width": int(match["cell_width"]),
        "byte_order": match.get("byte_order") or "little",
    }
    if match.get("stride"):
        table["stride"] = int(match["stride"])
    return table


class DiffScreen(Screen):
    """Compare two dumps and show what changed, table by table."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("s", "pick_stock", "Stock file", show=True),
        Binding("t", "pick_tuned", "Tuned file", show=True),
        Binding("enter", "primary", "Compare", show=True),
        # Contextual: each is advertised only in a state where it does something.
        Binding("d", "detail", "", show=False),
        Binding("p", "picture", "", show=False),
        Binding("x", "export_csv", "", show=False),
        Binding("u", "unexplained", "", show=False),
        Binding("m", "reshaped", "", show=False),
        Binding("f", "filter", "", show=False),
        Binding("w", "whole_file", "", show=False),
        Binding("c", "cook_pair", "", show=False),
        Binding("up", "move(-1)", "", show=False),
        Binding("down", "move(1)", "", show=False),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, stock: str | None = None, tuned: str | None = None) -> None:
        super().__init__()
        self.stock = stock
        self.tuned = tuned
        self.result: dict[str, Any] | None = None
        self.scan_ms = 0.0
        self.view = "tables"
        self.filter = FILTERS[0]
        self.whole_file = False
        self.cursor = 0
        self.identity: dict[str, str | None] = {}
        self._identifying = False
        self._notice: Text | None = None
        #: `[D]`: stock rows, tuned rows, and the group whose axis values they share.
        self.detail: tuple[list[list[int]], list[list[int]], dict] | None = None
        #: `[U]`: the region list, and the hex window of the one under the cursor.
        self.regions: list[dict[str, Any]] = []
        self.hexdiff: tuple[bytes, bytes, int] | None = None
        self.region_cursor = 0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="diff-panel"):
            yield Static("", id="diff-title")
            yield Static("", id="diff-stock")
            yield Static("", id="diff-tuned")
            yield Static("", id="diff-basis")
            yield ActivityBar(id="diff-activity")
            yield Static("", id="diff-body")
            yield Static("", id="diff-hints")

    def on_mount(self) -> None:
        self.query_one("#diff-title", Static).update(
            Text("DIFF — what changed between two dumps, table by table", style="#5a6b85")
        )
        if self.can_compare:
            self._read_identities()
        self._paint()

    # -- state ----------------------------------------------------------
    @property
    def can_compare(self) -> bool:
        return bool(self.stock and self.tuned)

    @property
    def groups(self) -> list[dict]:
        return list((self.result or {}).get("groups") or [])

    def _group_of(self, match: dict[str, Any]) -> dict:
        index = match.get("group")
        groups = self.groups
        if isinstance(index, int) and 0 <= index < len(groups):
            return groups[index]
        return {}

    def _has_axis(self, match: dict[str, Any]) -> bool:
        group = self._group_of(match)
        return bool(group.get("x_axis") and group.get("y_axis"))

    def rows(self) -> list[dict[str, Any]]:
        """The filtered, ranked list the cursor moves through."""
        matches = list((self.result or {}).get("matches") or [])
        if self.filter == "changed":
            matches = [m for m in matches if (m.get("changed_cells") or 0) > 0]
        elif self.filter == "realigned":
            matches = [
                m for m in matches
                if m.get("realigned") and (m.get("changed_cells") or 0) > 0
            ]
        elif self.filter == "no axis":
            matches = [
                m for m in matches
                if (m.get("changed_cells") or 0) > 0 and not self._has_axis(m)
            ]
        # Ranked: changed first, then tables with a measured axis before ones
        # inferred from the changed bytes, then by how far a cell moved.
        #
        # The axis comes before the percentage on purpose. A no-axis table's
        # "-100%/+100%" is computed against row shapes the scanner guessed, so it
        # is the least trustworthy number on the screen; sorting by percentage
        # alone puts exactly those rows at the top.
        order = sorted(
            matches,
            key=lambda m: (
                (m.get("changed_cells") or 0) <= 0,
                not self._has_axis(m),
                -abs(float(m.get("max_pct") or 0.0)),
                -abs(float(m.get("max_abs") or 0.0)),
            ),
        )
        return order

    @property
    def selected(self) -> dict[str, Any] | None:
        rows = self.rows()
        if not rows:
            return None
        return rows[max(0, min(self.cursor, len(rows) - 1))]

    # -- inputs ---------------------------------------------------------
    def _file_line(self, label: str, key: str, value: str | None, style: str) -> Text:
        line = Text()
        line.append(f"  {label:<7}", style="#3a4557")
        line.append(f"[{key}]", style="#4ad6a8")
        line.append("  ", style="#1e2a38")
        if not value:
            line.append("choose a file", style="#5a6b85")
            return line
        path = Path(value)
        line.append(_short(path.name), style=style)
        try:
            line.append(f"   {_size_label(path.stat().st_size)}", style="#3a4557")
        except OSError:
            line.append("   missing", style="bold #ff6b6b")
            return line
        known = self.identity.get(label)
        if known:
            line.append(f"   {known}", style="#8aa0c0")
        elif self._identifying:
            line.append("   identifying…", style="#3a4557")
        return line

    def _paint_basis(self) -> None:
        basis = self.query_one("#diff-basis", Static)
        if not self.can_compare:
            basis.update(
                Text(
                    "  The same two files COOK takes — one before the edit, one after.",
                    style="#3a4557",
                )
            )
            return
        line = Text()
        stock, tuned = Path(self.stock or ""), Path(self.tuned or "")
        same = (
            stock.resolve() == tuned.resolve()
            if stock.exists() and tuned.exists()
            else False
        )
        if same:
            line.append("  ✗ the same file is selected twice", style="#ffb454")
            line.append(" — there is nothing to compare", style="#3a4557")
        else:
            try:
                left, right = stock.stat().st_size, tuned.stat().st_size
            except OSError:
                left = right = -1
            if left != right:
                line.append("  ✗ different sizes", style="#ffb454")
                line.append(
                    f" — {_size_label(left)} vs {_size_label(right)}; "
                    "a diff needs the same image",
                    style="#3a4557",
                )
            else:
                line.append("  ✓ same size", style="#4ad6a8")
                stock_key = self.identity.get("stock")
                tuned_key = self.identity.get("tuned")
                if stock_key and tuned_key and stock_key == tuned_key:
                    line.append("   ·   ", style="#1e2a38")
                    line.append("same firmware", style="#4ad6a8")
                    line.append(f"   {stock_key}", style="#5a6b85")
                elif stock_key and tuned_key:
                    line.append("   ·   ", style="#1e2a38")
                    line.append("different firmware", style="#ffb454")
                    line.append(" — comparable, but this is a cross-revision diff",
                                style="#3a4557")
        basis.update(line)

    # -- painting -------------------------------------------------------
    def _paint(self) -> None:
        self.query_one("#diff-stock", Static).update(
            self._file_line("stock", "S", self.stock, "#c8d1e0")
        )
        self.query_one("#diff-tuned", Static).update(
            self._file_line("tuned", "T", self.tuned, "#c8d1e0")
        )
        self._paint_basis()
        self._paint_body()
        self._paint_hints()

    def _paint_body(self) -> None:
        body = self.query_one("#diff-body", Static)
        if self.result is None:
            body.update(self._notice if self._notice is not None else self._idle_text())
            return
        painter = {
            "detail": self._detail_text,
            "unexplained": self._unexplained_text,
            "reshaped": self._reshaped_text,
        }.get(self.view, self._tables_text)
        body.update(painter())

    def _notice_line(self, message: str, *, error: bool = False) -> None:
        self._notice = Text(
            f"  {message}\n", style="bold #ff6b6b" if error else "#ffb454"
        )
        self._paint_body()

    def _idle_text(self) -> Text:
        text = Text()
        if not self.can_compare:
            missing = "stock" if not self.stock else "tuned"
            text.append("  → press ", style="#5a6b85")
            text.append("[S]" if missing == "stock" else "[T]", style="bold #4ad6a8")
            text.append(f" to choose the {missing} file\n", style="#5a6b85")
            return text
        text.append(
            "  Scans both dumps for table-shaped regions, matches them across the pair,\n"
            "  and reports which ones moved. Nothing is named: an offset, a shape and a\n"
            "  measured axis range, nothing else.\n\n",
            style="#3a4557",
        )
        text.append("  → press ", style="#5a6b85")
        text.append("[Enter]", style="bold #4ad6a8")
        text.append(" to compare", style="#5a6b85")
        text.append("   (5–6 s for 4 MB: it really does scan both files)", style="#3a4557")
        return text

    # -- tables view ----------------------------------------------------
    def _summary(self, text: Text) -> None:
        result = self.result or {}
        matches = list(result.get("matches") or [])
        changed = [m for m in matches if (m.get("changed_cells") or 0) > 0]
        cells = sum(int(m.get("changed_cells") or 0) for m in changed)
        regions = self.regions
        largest = max((int(r.get("size") or 0) for r in regions), default=0)
        hidden = int(result.get("stock_tables_hidden") or 0)
        matched = int(result.get("matched_count") or 0)
        mode = "whole file" if self.whole_file else "calibration region"

        text.append(
            f"  scan {self.scan_ms / 1000:.1f} s · {matched:,} tables matched of "
            f"{int(result.get('stock_tables') or 0):,} / "
            f"{int(result.get('tuned_tables') or 0):,} · {mode}\n",
            style="#5a6b85",
        )
        if hidden and not self.whole_file:
            text.append(
                f"  {hidden:,} tables per side sit outside the calibration region and "
                "were not matched\n",
                style="#3a4557",
            )

        def row(label: str, value: str, note: str, key: str, style: str) -> None:
            # 15, not 14: "NOT IN A TABLE" is exactly 14 characters and ran into
            # the value column with no space of its own.
            text.append(f"  {label:<15}", style="#3a4557")
            text.append(f"{value:<24}", style=style)
            text.append(f"{note:<29}", style="#5a6b85")
            if key:
                text.append(f" [{key}]", style="#4ad6a8")
            text.append("\n")

        row("CHANGED", f"{len(changed)} tables · {cells:,} cells",
            "cells whose value moved", "", "bold #c8d1e0")
        # Deliberately not a byte total: a region is an *extent*, and its size
        # includes bytes that did not change. Adding the extents to the table
        # bytes would invent a number nobody can check.
        row("NOT IN A TABLE", f"{len(regions)} regions",
            f"largest {largest:,} B" if regions else "none",
            "U" if regions else "", "#ffb454" if regions else "#5a6b85")
        only_stock = int(result.get("only_in_stock_count") or 0)
        only_tuned = int(result.get("only_in_tuned_count") or 0)
        row("RESHAPED", f"{only_stock} stock · {only_tuned} tuned",
            "no match in the other file",
            "M" if (only_stock or only_tuned) else "",
            "#c8d1e0" if (only_stock or only_tuned) else "#5a6b85")
        row("UNCHANGED", f"{max(0, matched - len(changed)):,} tables",
            "matched, no cell moved", "F", "#5a6b85")
        text.append("\n")

    def _tables_text(self) -> Text:
        text = Text()
        self._summary(text)
        rows = self.rows()
        if not rows:
            text.append(f"  nothing matches: {FILTER_LABEL[self.filter]}\n", style="#ffb454")
            text.append("  [F] cycle the filter\n", style="#3a4557")
            return text

        text.append(f"  {self.filter} — {FILTER_LABEL[self.filter]} · {len(rows):,}\n",
                    style="#8aa0c0")
        text.append("   Δ MAX    Δ AVG    OFFSET       DIM    CHANGED   FLAGS\n",
                    style="#5a6b85")
        start = max(0, min(self.cursor - WINDOW // 2, max(0, len(rows) - WINDOW)))
        for index in range(start, min(len(rows), start + WINDOW)):
            match = rows[index]
            here = index == self.cursor
            style = "bold #ffffff" if here else "#c8d1e0"
            text.append(" ▸" if here else "  ", style="#4ad6a8")
            text.append(f" {float(match.get('max_pct') or 0.0):+6.1f}%", style=style)
            text.append(f" {float(match.get('avg_pct') or 0.0):+6.1f}%", style="#8aa0c0")
            text.append(f"  0x{int(match['offset_stock']):08X}", style=style)
            text.append(f"  {int(match['cols']):>2}×{int(match['rows']):<2}", style="#8aa0c0")
            text.append(
                f" {int(match.get('changed_cells') or 0):>3}/{int(match.get('total_cells') or 0):<3}",
                style=style,
            )
            flags = []
            if match.get("realigned"):
                flags.append("realigned")
            if not self._has_axis(match):
                flags.append("no axis")
            if match.get("suspicious"):
                flags.append("look-alike")
            if flags:
                text.append(f"  {', '.join(flags)}", style="#ffb454")
            text.append("\n")
        text.append(f"\n  {self.cursor + 1}/{len(rows):,}   ", style="#5a6b85")
        for key, what in (
            ("↑↓", "move"),
            ("D", "cells"),
            ("M", "reshaped"),
            ("W", "whole file" if not self.whole_file else "region only"),
            ("C", "cook"),
        ):
            if what != "move":
                text.append(" · ", style="#1e2a38")
            text.append(f"[{key}]", style="#4ad6a8")
            text.append(f" {what}", style="#5a6b85")
        text.append("\n")
        return text

    # -- detail view ----------------------------------------------------
    @staticmethod
    def _axis_at(axis: list[int], index: int) -> str:
        return str(axis[index]) if index < len(axis) else str(index)

    def _detail_text(self) -> Text:
        text = Text()
        match = self.selected
        if match is None or self.detail is None:
            text.append("  nothing selected\n", style="#ffb454")
            return text
        stock_rows, tuned_rows, group = self.detail
        x_axis = [int(v) for v in (group.get("x_axis") or [])]
        y_axis = [int(v) for v in (group.get("y_axis") or [])]
        width = int(match.get("cell_width") or 2)
        has_axis = bool(x_axis and y_axis)

        text.append(
            f"  0x{int(match['offset_stock']):08X} · {int(match['cols'])}×"
            f"{int(match['rows'])} · {width} B "
            f"{'LE' if match.get('byte_order') == 'little' else 'BE'}",
            style="bold #c8d1e0",
        )
        if match.get("realigned"):
            text.append(" · realigned", style="#ffb454")
        if not has_axis:
            text.append(" · no axis", style="#ffb454")
        text.append("\n")
        text.append(
            "  axis measured from the binary — breakpoints, not indices\n"
            if has_axis
            else "  no stored axis: the labels below are cell indices\n",
            style="#3a4557",
        )
        text.append("\n")

        columns = min(int(match["cols"]), 8)
        label_width = max((len(str(v)) for v in y_axis), default=2)
        label_width = min(max(label_width, 2), 8)
        cell_width = 7

        header = Text("  " + " " * (label_width + 2), style="#3a4557")
        for col in range(columns):
            header.append(f"{self._axis_at(x_axis, col) if has_axis else col:>{cell_width}}",
                          style="#5a6b85")
        if int(match["cols"]) > columns:
            header.append(f"   …+{int(match['cols']) - columns}", style="#3a4557")
        text.append(header)
        text.append("\n")

        shown_rows = min(int(match["rows"]), 12)

        def prefix(label_text: str, name: str) -> str:
            """Row label on the first line of the block, the side's name on each."""
            return f"  {label_text:>{label_width}} {name:<6}"

        for row_index in range(shown_rows):
            label = y_axis[row_index] if row_index < len(y_axis) else row_index
            for name, grid, style in (
                ("stock", stock_rows, "#c8d1e0"),
                ("tuned", tuned_rows, "#8aa0c0"),
            ):
                line = Text(
                    prefix(str(label) if name == "stock" else "", name), style="#3a4557"
                )
                for col in range(columns):
                    value = (
                        grid[row_index][col]
                        if row_index < len(grid) and col < len(grid[row_index])
                        else 0
                    )
                    line.append(f"{value:>{cell_width}}", style=style)
                text.append(line)
                text.append("\n")
            delta_line = Text(prefix("", "Δ"), style="#3a4557")
            for col in range(columns):
                before = (
                    stock_rows[row_index][col]
                    if row_index < len(stock_rows) and col < len(stock_rows[row_index])
                    else 0
                )
                after = (
                    tuned_rows[row_index][col]
                    if row_index < len(tuned_rows) and col < len(tuned_rows[row_index])
                    else 0
                )
                delta = after - before
                colour = "#4ad6a8" if delta > 0 else ("#ff6b6b" if delta < 0 else "#3a4557")
                delta_line.append(f"{delta:>+{cell_width}}" if delta else f"{'·':>{cell_width}}",
                                  style=colour)
            text.append(delta_line)
            text.append("\n")
        if int(match["rows"]) > shown_rows:
            text.append(f"  … {int(match['rows']) - shown_rows} more rows\n",
                        style="#3a4557")

        text.append(
            f"\n  max Δ {float(match.get('max_pct') or 0):+.1f}% · "
            f"avg {float(match.get('avg_pct') or 0):+.1f}% · "
            f"{int(match.get('changed_cells') or 0)}/{int(match.get('total_cells') or 0)} "
            "cells moved\n",
            style="#8aa0c0",
        )
        text.append("  [P] picture · [X] csv of the delta · [Esc] back to the list\n",
                    style="#5a6b85")
        return text

    # -- unexplained / reshaped views ------------------------------------
    def _unexplained_text(self) -> Text:
        text = Text()
        if not self.regions:
            text.append("  no changed region without a table\n", style="#4ad6a8")
            text.append("  [Esc] back\n", style="#5a6b85")
            return text
        text.append(f"  Changed regions no table accounts for — {len(self.regions)}\n",
                    style="#8aa0c0")
        text.append(
            "  A region is an extent, not a byte count: it can contain bytes that did\n"
            "  not change. The size is what the scanner saw, nothing more.\n\n",
            style="#3a4557",
        )
        text.append("   OFFSET       SIZE\n", style="#5a6b85")
        start = max(
            0, min(self.region_cursor - WINDOW // 2, max(0, len(self.regions) - WINDOW))
        )
        for index in range(start, min(len(self.regions), start + WINDOW)):
            region = self.regions[index]
            here = index == self.region_cursor
            text.append(" ▸" if here else "  ", style="#4ad6a8")
            text.append(
                f"  0x{int(region['offset']):08X}   {int(region['size']):>6,} B",
                style="bold #ffffff" if here else "#c8d1e0",
            )
            text.append("\n")

        if self.hexdiff is not None:
            stock_bytes, tuned_bytes, offset = self.hexdiff
            text.append(
                f"\n  hex diff 0x{offset:08X} · {len(stock_bytes)} B"
                "   stock / tuned, changed bytes marked\n",
                style="#8aa0c0",
            )
            for start_at in range(0, len(stock_bytes), 16):
                chunk_stock = stock_bytes[start_at : start_at + 16]
                chunk_tuned = tuned_bytes[start_at : start_at + 16]
                text.append(f"  {offset + start_at:08X}  ", style="#5a6b85")
                text.append(" ".join(f"{b:02X}" for b in chunk_stock), style="#c8d1e0")
                text.append("\n            ", style="#5a6b85")
                for index, before in enumerate(chunk_stock):
                    after = chunk_tuned[index] if index < len(chunk_tuned) else None
                    if after is None:
                        text.append("   ", style="#3a4557")
                    elif before == after:
                        text.append("-- ", style="#3a4557")
                    else:
                        text.append(f"{after:02X} ", style="#ffb454")
                text.append("\n")
        text.append(
            f"\n  {self.region_cursor + 1}/{len(self.regions):,}   "
            "[⏎] hex diff · [↑↓] move · [Esc] back\n",
            style="#5a6b85",
        )
        return text

    def _reshaped_text(self) -> Text:
        text = Text()
        result = self.result or {}
        for label, key in (
            ("Only in stock", "only_in_stock"),
            ("Only in tuned", "only_in_tuned"),
        ):
            entries = list(result.get(key) or [])
            text.append(f"  {label} — {len(entries)}\n", style="#8aa0c0")
            text.append(
                "  No counterpart in the other file: the grid changed shape, so the two\n"
                "  sides cannot be matched cell for cell.\n",
                style="#3a4557",
            )
            for entry in entries[:12]:
                text.append(
                    f"   0x{int(entry['offset']):08X}   {int(entry['cols'])}×"
                    f"{int(entry['rows'])} · {int(entry['cell_width'])} B\n",
                    style="#c8d1e0",
                )
            if len(entries) > 12:
                text.append(f"   … +{len(entries) - 12} more\n", style="#3a4557")
            text.append("\n")
        text.append("  [Esc] back\n", style="#5a6b85")
        return text

    # -- hints ----------------------------------------------------------
    def _paint_hints(self) -> None:
        hints = Text("  ")
        hints.append("[S]", style="#4ad6a8")
        hints.append(" stock   ", style="#3a4557")
        hints.append("[T]", style="#4ad6a8")
        hints.append(" tuned", style="#3a4557")
        result = self.result or {}
        if self.result is None and self.can_compare:
            hints.append("    ", style="#1e2a38")
            hints.append("[Enter]", style="#4ad6a8")
            hints.append(" compare", style="#3a4557")
        if self.result is not None:
            if self.view == "tables":
                hints.append("   ", style="#1e2a38")
                hints.append("[F]", style="#4ad6a8")
                hints.append(f" {self.filter}", style="#3a4557")
                if int(result.get("unidentified_changed_count") or 0):
                    hints.append("   ", style="#1e2a38")
                    hints.append("[U]", style="#ffb454")
                    hints.append(" unexplained", style="#3a4557")
            elif self.view == "unexplained":
                hints.append("    ", style="#1e2a38")
                hints.append("[Enter]", style="#4ad6a8")
                hints.append(" hex diff", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" back", style="#3a4557")
        self.query_one("#diff-hints", Static).update(hints)

    # -- work: comparison -----------------------------------------------
    def action_pick_stock(self) -> None:
        def _chosen(path) -> None:
            if path is not None:
                self.stock = str(path)
                self._reset()
                self._read_identities()
                self._paint()

        start = Path(self.stock).parent if self.stock else None
        self.app.push_screen(FilePicker(start), _chosen)

    def action_pick_tuned(self) -> None:
        def _chosen(path) -> None:
            if path is not None:
                self.tuned = str(path)
                self._reset()
                self._read_identities()
                self._paint()

        start = Path(self.tuned).parent if self.tuned else None
        self.app.push_screen(FilePicker(start), _chosen)

    def _reset(self) -> None:
        self.result = None
        self.detail = None
        self.regions = []
        self.hexdiff = None
        self.cursor = 0
        self.region_cursor = 0
        self.view = "tables"
        self._notice = None

    def _read_identities(self) -> None:
        if not self.can_compare:
            return
        self._identifying = True
        self.run_worker(self._identify(), name="diff-identify", exclusive=True)

    async def _identify(self) -> None:
        import asyncio

        from openremap.api import call

        async def one(side: str, path: str | None) -> tuple[str, str | None]:
            if not path:
                return side, None
            try:
                result = await asyncio.to_thread(call, "identify", {"path": path})
            except Exception:  # noqa: BLE001 - an unreadable file is not a crash
                return side, None
            return side, result.get("match_key")

        pairs = await asyncio.gather(one("stock", self.stock), one("tuned", self.tuned))
        self.identity = {side: key for side, key in pairs}
        self._identifying = False
        self._paint()

    def action_compare(self) -> None:
        if not self.can_compare:
            self.app.notify("choose both files first", timeout=3)
            return
        self._reset()
        self.run_worker(self._scan(), name="diff-scan", exclusive=True)

    async def _scan(self) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#diff-activity", ActivityBar)
        activity.start("scanning both dumps for tables")
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                "diff_maps",
                {
                    "stock_path": self.stock,
                    "tuned_path": self.tuned,
                    # Every matched table: which ones to show is the filter's job,
                    # not a hidden `top 50` decided before the user sees anything.
                    "top": 4000,
                    "threshold": 0.0,
                    "whole_file": self.whole_file,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            activity.stop()
            self._notice_line(f"compare failed: {type(exc).__name__}: {exc}", error=True)
            return
        self.scan_ms = (time.perf_counter() - started) * 1000
        self.result = dict(result)
        self.regions = sorted(
            result.get("unidentified_changed") or [],
            key=lambda region: -int(region.get("size") or 0),
        )
        self.cursor = 0
        self.region_cursor = 0
        self._notice = None
        activity.stop(f"compared in {self.scan_ms:.0f} ms")
        self._paint()

    # -- work: the selected table ---------------------------------------
    def _load_detail(self) -> bool:
        """Read both sides of the selected table — one table, twice."""
        from openremap.api import call

        match = self.selected
        if match is None:
            return False
        try:
            stock = call("map_grid", {"path": self.stock, "table": _table_of(match)})
            tuned = call(
                "map_grid",
                {
                    "path": self.tuned,
                    "table": _table_of(match, offset=int(match["offset_tuned"])),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._notice_line(
                f"could not read the cells: {type(exc).__name__}: {exc}", error=True
            )
            return False
        self.detail = (
            [list(row) for row in stock["rows"]],
            [list(row) for row in tuned["rows"]],
            self._group_of(match),
        )
        return True

    def action_detail(self) -> None:
        if self.result is None:
            self.app.notify("compare first", timeout=3)
            return
        if self.selected is None:
            self.app.notify("no table in this filter", timeout=3)
            return
        if not self._load_detail():
            return
        self.view = "detail"
        self._paint()

    def action_picture(self) -> None:
        """Write both sides of the selected table as SVG pictures."""
        if self.selected is None:
            self.app.notify("no table in this filter", timeout=3)
            return
        if self.detail is None and not self._load_detail():
            return
        from openremap.api import call

        match = self.selected or {}
        group = self._group_of(match)
        base = _table_of(match)
        if group.get("x_axis") and group.get("y_axis"):
            # The diff already measured the breakpoints; the picture must show the
            # same ones rather than re-deriving them and falling back to indices.
            base["x_axis"] = [int(v) for v in group["x_axis"]]
            base["y_axis"] = [int(v) for v in group["y_axis"]]
        written: list[str] = []
        for side, path, offset in (
            ("stock", self.stock, int(match["offset_stock"])),
            ("tuned", self.tuned, int(match["offset_tuned"])),
        ):
            table = dict(base)
            table["offset"] = offset
            name = Path(path or "dump").name
            target = paths.subdir("exports") / (
                f"{Path(path or 'dump').stem}-0x{offset:08X}-{side}.svg"
            )
            try:
                picture = call(
                    "map_image",
                    {"path": path, "table": table, "name": f"{name} 0x{offset:08X} {side}"},
                )
                target.write_text(picture["svg"], encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                self.app.notify(f"picture failed: {type(exc).__name__}: {exc}", timeout=5)
                return
            written.append(target.name)
        self.app.notify(f"pictures written: {', '.join(written)}", timeout=5)

    def action_export_csv(self) -> None:
        """Write the delta of the selected table as long-form CSV."""
        if self.selected is None:
            self.app.notify("no table in this filter", timeout=3)
            return
        if self.detail is None and not self._load_detail():
            return
        import csv as _csv

        stock_rows, tuned_rows, group = self.detail or ([], [], {})
        match = self.selected or {}
        x_axis = [int(v) for v in (group.get("x_axis") or [])]
        y_axis = [int(v) for v in (group.get("y_axis") or [])]
        target = paths.subdir("exports") / (
            f"{Path(self.stock or 'stock').stem}-0x{int(match['offset_stock']):08X}.csv"
        )
        try:
            with target.open("w", newline="", encoding="utf-8") as handle:
                writer = _csv.writer(handle)
                writer.writerow(
                    ["row", "col", "y_breakpoint", "x_breakpoint", "stock", "tuned", "delta"]
                )
                for row_index, stock_row in enumerate(stock_rows):
                    tuned_row = tuned_rows[row_index] if row_index < len(tuned_rows) else []
                    for col, before in enumerate(stock_row):
                        after = tuned_row[col] if col < len(tuned_row) else 0
                        writer.writerow(
                            [
                                row_index,
                                col,
                                y_axis[row_index] if row_index < len(y_axis) else "",
                                x_axis[col] if col < len(x_axis) else "",
                                before,
                                after,
                                after - before,
                            ]
                        )
        except OSError as exc:
            self.app.notify(f"could not write {target.name}: {exc}", timeout=5)
            return
        self.app.notify(f"CSV written: {target.name}", timeout=4)

    # -- views + navigation ---------------------------------------------
    def action_filter(self) -> None:
        if self.result is None:
            self.app.notify("compare first", timeout=3)
            return
        self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
        self.cursor = 0
        self.view = "tables"
        self._paint()

    def action_whole_file(self) -> None:
        """Toggle the calibration-region default and re-scan.

        Not a stored preference: the next comparison uses whichever mode is showing,
        and the summary always says which — a hidden mode would make two runs
        disagree for no visible reason.
        """
        self.whole_file = not self.whole_file
        if self.result is not None:
            self.action_compare()
        else:
            self._paint()

    def action_unexplained(self) -> None:
        if self.result is None:
            self.app.notify("compare first", timeout=3)
            return
        if not self.regions:
            self.app.notify("every changed byte falls inside a table", timeout=4)
            return
        self.view = "tables" if self.view == "unexplained" else "unexplained"
        self.hexdiff = None
        self._paint()

    def action_reshaped(self) -> None:
        if self.result is None:
            self.app.notify("compare first", timeout=3)
            return
        self.view = "tables" if self.view == "reshaped" else "reshaped"
        self._paint()

    def action_move(self, delta: int) -> None:
        if self.result is None:
            return
        if self.view == "unexplained":
            if not self.regions:
                return
            self.region_cursor = max(
                0, min(self.region_cursor + delta, len(self.regions) - 1)
            )
            self.hexdiff = None
            self._paint_body()
            return
        if self.view != "tables":
            return
        rows = self.rows()
        if not rows:
            return
        self.cursor = max(0, min(self.cursor + delta, len(rows) - 1))
        self._paint_body()

    def action_primary(self) -> None:
        """[Enter] means what the current view is about."""
        if self.view == "unexplained":
            self._read_hex_window()
            return
        self.action_compare()

    def _read_hex_window(self) -> None:
        """Both sides of the region under the cursor."""
        if not self.regions:
            return
        from openremap.api import call

        region = self.regions[self.region_cursor]
        offset = int(region["offset"])
        length = min(int(region["size"]), HEX_WINDOW)
        try:
            stock = call(
                "hex_region", {"path": self.stock, "offset": offset, "length": length}
            )
            tuned = call(
                "hex_region", {"path": self.tuned, "offset": offset, "length": length}
            )
        except Exception as exc:  # noqa: BLE001
            self.app.notify(
                f"could not read the bytes: {type(exc).__name__}: {exc}", timeout=5
            )
            return
        self.hexdiff = (bytes.fromhex(stock["hex"]), bytes.fromhex(tuned["hex"]), offset)
        self._paint_body()

    def action_cook_pair(self) -> None:
        """Hand the same pair to COOK — one pick, two questions."""
        if not self.can_compare:
            self.app.notify("choose both files first", timeout=3)
            return
        from openremap.tui.screens.cook import CookScreen

        self.app.push_screen(CookScreen(stock=self.stock, tuned=self.tuned))

    def action_back(self) -> None:
        if self.view != "tables":
            self.view = "tables"
            self.hexdiff = None
            self._paint()
            return
        self.app.pop_screen()
