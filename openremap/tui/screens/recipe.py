"""
The recipe detail — what is actually inside a .remap file.

``recipe_info`` already answers "what does this recipe claim". This screen shows
the other half: the list itself. Each row is one instruction — where it writes,
how many bytes, which flash region it lands in, and which flags it carries — so
"79 instructions · 7,828 bytes" stops being a number and becomes something you
can scroll.

Read-only, and honest about being a view: the list is capped (a recipe can carry
thousands) and says so when it is.
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

__all__ = ["RecipeDetailScreen"]

#: Region → colour, so the eye can find the ones outside calibration.
_REGION_COLOUR = {
    "calibration": "#4ad6a8",
    "mixed": "#ffb454",
    "erased": "#5a6b85",
    "code": "#ff6b6b",
}


class RecipeDetailScreen(ModalScreen[None]):
    """A recipe's facts plus its instruction list."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", show=True),
    ]

    def __init__(self, info: dict[str, Any]) -> None:
        super().__init__()
        self.info = info

    def compose(self) -> ComposeResult:
        with Vertical(id="recipe-panel"):
            yield Static("", id="recipe-title")
            yield Static("", id="recipe-facts")
            yield DataTable(id="recipe-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="recipe-hints")

    def on_mount(self) -> None:
        self.query_one("#recipe-title", Static).update(
            Text(f"RECIPE — {self.info.get('name') or 'recipe.remap'}", style="#5a6b85")
        )
        self._paint_facts()
        self._paint_rows()
        hints = Text("  ")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" close", style="#3a4557")
        self.query_one("#recipe-hints", Static).update(hints)

    def action_close(self) -> None:
        self.dismiss(None)

    def _paint_facts(self) -> None:
        info = self.info
        contract = info.get("contract") or {}
        source = info.get("source") or {}
        tune = info.get("tune") or {}
        producer = info.get("producer") or {}
        statistics = info.get("statistics") or {}

        text = Text()
        text.append(f"  {info.get('instructions')} instructions", style="#c8d1e0")
        text.append(f" · {int(info.get('bytes_written') or 0):,} bytes", style="#8aa0c0")
        text.append(f" · schema {info.get('schema')}", style="#5a6b85")
        text.append(f" · {producer.get('command') or '?'}\n", style="#5a6b85")
        text.append(
            f"  identity_floor {contract.get('identity_floor')} · anchors_unique "
            f"{contract.get('anchors_unique')} · sections "
            f"{', '.join(contract.get('sections') or []) or 'none'}\n",
            style="#8aa0c0",
        )
        if source.get("filename"):
            text.append(
                f"  from  {source.get('filename')}  ({source.get('size')} B · "
                f"{source.get('match_key') or 'unidentified'})\n",
                style="#5a6b85",
            )
        if tune.get("filename"):
            text.append(f"  to    {tune.get('filename')}\n", style="#5a6b85")
        if statistics:
            text.append(
                f"  changed {int(statistics.get('total_bytes_changed') or 0):,} bytes "
                f"({float(statistics.get('percentage_changed') or 0):.2f} %) · "
                f"{statistics.get('single_byte_changes', 0)} single / "
                f"{statistics.get('multi_byte_changes', 0)} multi-byte · "
                f"largest {statistics.get('largest_change_size', 0)} B\n",
                style="#5a6b85",
            )
        self.query_one("#recipe-facts", Static).update(text)

    def _paint_rows(self) -> None:
        table = self.query_one("#recipe-table", DataTable)
        table.add_columns("#", "offset", "bytes", "region", "anchor", "flags")
        rows = self.info.get("instruction_rows") or []
        if not rows:
            table.add_row("", "", "", "", "", "this view was opened without the list")
            return
        for row in rows:
            region = str(row.get("region") or "—")
            flags = ", ".join(row.get("flags") or []) or ""
            table.add_row(
                str(row.get("index")),
                f"0x{int(row.get('offset') or 0):06X}",
                str(row.get("size")),
                Text(region, style=_REGION_COLOUR.get(region, "#8aa0c0")),
                "unique" if row.get("ctx_unique") else Text("weak", style="#ffb454"),
                flags,
            )
        if self.info.get("instructions_truncated"):
            table.add_row("…", "", "", "", "", "list truncated by the reader")
