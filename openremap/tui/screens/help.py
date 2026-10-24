"""
The HELP screen — what this tool can do, generated from the engine itself.

Two questions, two answers, both read at runtime:

* **What can it do?** ``list_methods`` from the API registry — every command,
  composite and workflow the engine exposes, with its own one-line description.
  Nothing here is hand-written, so it cannot drift from the code: a method that
  is added shows up, one that is removed disappears.
* **What can it recognise?** The curated family reference (era, typical sizes,
  the vehicles each family shipped in, and the identifier it is found by). That
  is reference material, *not* detection — a family is only claimed when an
  extractor fires.

The screen is read-only on purpose: it is the place a newcomer lands when they do
not yet know which of the six tasks they want.
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Static

__all__ = ["HelpScreen"]


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"

#: Registry kinds, in the order they are shown, with the heading for each.
_KINDS: tuple[tuple[str, str], ...] = (
    ("command", "commands — one job each"),
    ("composite", "composites — a pipeline over several services"),
    ("workflow", "workflows — a declared pipeline"),
)


class HelpScreen(Screen):
    """What the engine can do, and which families it recognises."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back", show=True),
        Binding("f", "families", "Families", show=True),
        Binding("c", "capabilities", "Capabilities", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[dict[str, Any]] = []
        self.families: list[dict[str, Any]] = []
        self.registry: list[dict[str, Any]] = []
        self.declared: list[dict[str, Any]] = []
        self.extractors = 0
        self.family_names = 0
        self.view = "capabilities"
        #: The summary line, kept so a selected family can be shown *under* it
        #: instead of replacing the counts.
        self._base_note: Text = Text()

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="help-panel"):
            yield Static("", id="help-title")
            yield Static("", id="help-note")
            yield DataTable(id="help-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="help-hints")

    def on_mount(self) -> None:
        self.query_one("#help-title", Static).update(
            Text("HELP — what this tool can do", style="#5a6b85")
        )
        self._load()
        self._paint()

    def _load(self) -> None:
        from openremap.api import call

        try:
            self.methods = list(call("list_methods", {}).get("methods") or [])
        except Exception:  # noqa: BLE001 - the screen degrades, it does not crash
            self.methods = []
        try:
            report = call("families", {})
            self.families = list(report.get("families") or [])
            self.registry = list(report.get("registry") or [])
            self.declared = list(report.get("declared") or [])
            self.extractors = int(report.get("extractors") or 0)
            self.family_names = int(report.get("family_names") or 0)
        except Exception:  # noqa: BLE001
            self.families = []
            self.registry = []
            self.declared = []
            self.extractors = 0
            self.family_names = 0

    # -- painting -------------------------------------------------------
    def _paint(self) -> None:
        table = self.query_one("#help-table", DataTable)
        table.clear(columns=True)
        note = Text()
        if self.view == "capabilities":
            table.add_columns("kind", "method", "what it does")
            for kind, heading in _KINDS:
                for method in [m for m in self.methods if m.get("kind") == kind]:
                    table.add_row(
                        kind,
                        str(method.get("name")),
                        str(method.get("description") or "")[:96],
                    )
            note.append(
                f"  {len(self.methods)} methods, read from the engine's registry — "
                "this list cannot go stale.",
                style="#5a6b85",
            )
            note.append(
                "\n  [F] shows every family the extractors declare, with what is "
                "written about each",
                style="#3a4557",
            )
        elif self.view == "families":
            table.add_columns("manufacturer", "family", "extractor", "strength", "reference")
            for row in self.declared:
                reference = row.get("documented_as")
                table.add_row(
                    str(row.get("manufacturer")),
                    str(row.get("family")),
                    str(row.get("extractor")),
                    str(row.get("strength")),
                    Text(str(reference), style="#4ad6a8") if reference else Text("—", style="#3a4557"),
                )
            documented = sum(1 for row in self.declared if row.get("documented_as"))
            note.append(
                f"  {self.family_names or len(self.declared)} families declared by "
                f"{self.extractors} extractors "
                f"across {len(self.registry)} manufacturers",
                style="#5a6b85",
            )
            note.append(
                f" · {documented} have a written reference (column 5). A family is "
                "claimed only when an extractor fires.",
                style="#3a4557",
            )
            note.append("\n  ↑↓ highlights a family and shows what is written about it", style="#3a4557")
        else:
            note.append("  ", style="#5a6b85")
        self._base_note = note
        self.query_one("#help-note", Static).update(note)
        self._paint_hints()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the written reference for the highlighted family, if there is one."""
        if self.view != "families":
            return
        if not (0 <= event.cursor_row < len(self.declared)):
            return
        row = self.declared[event.cursor_row]
        note = Text()
        note.append(f"  {row.get('family')}", style="#c8d1e0")
        note.append(f" · {row.get('manufacturer')} · {row.get('extractor')}", style="#5a6b85")
        if row.get("documented_as"):
            note.append(f"\n  described under {row['documented_as']}", style="#8aa0c0")
            facts = " · ".join(
                str(part) for part in (row.get("era"), row.get("size")) if part
            )
            if facts:
                note.append(f" — {facts}", style="#5a6b85")
            if row.get("identifier"):
                note.append(f"\n  {_short(str(row['identifier']), 100)}", style="#5a6b85")
        else:
            note.append(
                "\n  no written reference yet — detection only (the extractor fires on "
                "structure, but nothing is documented about it)",
                style="#3a4557",
            )
        combined = self._base_note.copy()
        combined.append("\n")
        combined.append_text(note)
        self.query_one("#help-note", Static).update(combined)

    def _paint_hints(self) -> None:
        hints = Text("  ")
        if self.view == "capabilities":
            hints.append("[F]", style="#4ad6a8")
            hints.append(" families", style="#3a4557")
        else:
            hints.append("[C]", style="#4ad6a8")
            hints.append(" capabilities", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" back", style="#3a4557")
        self.query_one("#help-hints", Static).update(hints)

    # -- actions ---------------------------------------------------------
    def action_families(self) -> None:
        self.view = "families"
        self._paint()

    def action_capabilities(self) -> None:
        self.view = "capabilities"
        #: The summary line, kept so a selected family can be shown *under* it
        #: instead of replacing the counts.
        self._base_note: Text = Text()
        self._paint()

    def action_back(self) -> None:
        self.app.pop_screen()
