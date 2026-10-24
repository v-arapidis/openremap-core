"""
The BATCH screen — what is in this folder, one dump at a time.

This is the TUI face of ``openremap scan``, and it exists because a folder of
dumps is not a list of equal things: some are identified, some are recognised
but carry no software number, some are claimed by two extractors, some are not
ECUs at all, and some are the same file twice. The screen classifies each file
and says which it is.

Two rules shape it:

* **Classify only.** Nothing is copied, moved or renamed here. Organising the
  folder is a second step with a preview of every destination, and it copies by
  default (moving is on ``M``), because a copy cannot lose a dump.
* **Real progress.** Classification is 6 ms for a 65 KB dump and 0.8 s for a
  4 MB one, so 200 files is about a minute. The screen shows how many are done,
  the measured pace, and what remains — and ``Esc`` stops and keeps what is
  already classified.

The loop lives here on purpose: the *rules* (which files, what a destination
means, where a file would be filed) are services, but how much of the folder to
do before answering the keyboard is a UI decision.
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
from openremap.tui.screens.picker import BINARY_SUFFIXES, FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["BatchScreen", "verdict_label"]

#: destination → (short label shown in the table, colour)
_VERDICT: dict[str, tuple[str, str]] = {
    "scanned": ("ok", "#4ad6a8"),
    "sw_missing": ("no-sw", "#ffb454"),
    "contested": ("contested", "#ffb454"),
    "unknown": ("unknown", "#5a6b85"),
    "error": ("error", "#ff6b6b"),
}


def _copies_cell(copies: int) -> Any:
    """How many copies an original has — nothing at all when it has none."""
    if copies <= 0:
        return ""
    return Text(f"×{copies}", style="#ffb454")


def verdict_label(destination: str) -> str:
    return _VERDICT.get(destination, (destination or "?", "#5a6b85"))[0]


class BatchScreen(Screen):
    """Classify every dump in a folder, then optionally file them."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "pick_folder", "Folder", show=True),
        # priority: the table (which holds focus) binds Enter to "select row",
        # a key that means nothing here — the screen's Enter must win.
        Binding("enter", "scan", "Scan", show=True, priority=True),
        Binding("i", "open_selected", "Identify", show=True),
        Binding("d", "toggle_subfolders", "Subfolders", show=True),
        Binding("v", "toggle_vins", "VINs", show=True),
        Binding("o", "organize", "Organize", show=True),
        Binding("escape", "back", "Back", show=True),
        # Contextual: only advertised in the states that allow them.
        Binding("c", "copy_files", "", show=False),
        Binding("m", "move_files", "", show=False),
    ]

    def __init__(self, folder: str | None = None) -> None:
        super().__init__()
        self.folder = folder
        self.rows: list[dict[str, Any]] = []
        self.candidates: list[Path] = []
        self.skipped = 0
        self.duplicates = 0
        #: md5 → the row that owns it, and how many copies exist for each owner.
        #: Kept so a duplicate can name the file it copies ("of #12") and the
        #: original can say how many copies it has.
        self._owners: dict[str, int] = {}
        self._copies: dict[int, int] = {}
        #: Display order → index into ``self.rows``. Grouping duplicates rewrites
        #: it, so anything that maps a cursor row to a file must go through it.
        self._display: list[int] = []
        self.running = False
        self.stopped = False
        self.elapsed_ms = 0.0
        self.plan: dict[str, Any] | None = None
        self.filed: dict[str, Any] | None = None
        #: Whether subfolders are included. A folder of folders (Bosch/EDC16/…)
        #: has no dumps at its own level, so this is offered the moment it is
        #: needed — see ``subfolders``.
        self.recursive = False
        #: VIN candidates are a separate scan (~3 ms for a small dump, ~170 ms for
        #: 4 MB), so they are opt-in rather than a silent 20 % tax on every batch.
        self.with_vins = False
        #: How many dumps a recursive walk would find, counted so the screen can
        #: say "nothing here, but 1126 in subfolders" instead of showing a void.
        self.subfolders = 0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="batch-panel"):
            yield Static("", id="batch-title")
            yield Static("", id="batch-folder")
            yield Static("", id="batch-summary")
            yield ActivityBar(id="batch-activity")
            with Horizontal(id="batch-body"):
                yield DataTable(id="batch-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="batch-plan", markup=False)
            yield Static("", id="batch-hints")

    def on_mount(self) -> None:
        table = self.query_one("#batch-table", DataTable)
        table.add_columns("#", "file", "size", "family", "software", "vin", "dup", "verdict")
        self.query_one("#batch-title", Static).update(
            Text("BATCH — classify every dump in a folder", style="#5a6b85")
        )
        if self.folder:
            self.run_worker(self._scan(), name="batch-scan", exclusive=True)
        self._paint()

    # -- painting --------------------------------------------------------
    def _paint(self) -> None:
        self._paint_folder()
        self._paint_summary()
        self._paint_plan()
        self._paint_hints()

    def _paint_folder(self) -> None:
        line = Text()
        line.append("  folder  ", style="#3a4557")
        line.append("[F]", style="#4ad6a8")
        line.append("  ", style="#1e2a38")
        if not self.folder:
            line.append("choose a folder", style="#5a6b85")
        else:
            line.append(paths.label(Path(self.folder)), style="#c8d1e0")
            if self.candidates:
                line.append(f"   {len(self.candidates)} dumps", style="#3a4557")
                if self.recursive:
                    line.append(" (with subfolders)", style="#3a4557")
                if self.skipped:
                    line.append(f" · {self.skipped} not a dump (left alone)", style="#3a4557")
        self.query_one("#batch-folder", Static).update(line)

    def _paint_summary(self) -> None:
        line = Text()
        if not self.rows and not self.running:
            if self.folder and self.candidates:
                line.append("  ready — ", style="#5a6b85")
                line.append(f"{len(self.candidates)} files", style="#4ad6a8")
                line.append(
                    " will be classified with every extractor. Nothing is moved.",
                    style="#3a4557",
                )
            elif self.folder:
                # An empty answer is still an answer — say why it is empty.
                line.append("  no dumps in this folder", style="#ffb454")
                if self.subfolders:
                    line.append(
                        f" — {self.subfolders} found in its subfolders "
                        "(press [D] to include them)",
                        style="#3a4557",
                    )
                else:
                    line.append(
                        " — nothing here has an extension the engine reads "
                        "(.bin .ori .hex .s19 .srec .mot)",
                        style="#3a4557",
                    )
            self.query_one("#batch-summary", Static).update(line)
            return

        counts: dict[str, int] = {}
        for row in self.rows:
            counts[str(row.get("destination"))] = counts.get(str(row.get("destination")), 0) + 1
        line.append(f"  {len(self.rows)}", style="#c8d1e0")
        line.append(f"/{len(self.candidates)} classified", style="#5a6b85")
        for destination in ("scanned", "sw_missing", "contested", "unknown", "error"):
            count = counts.get(destination, 0)
            if not count:
                continue
            label, colour = _VERDICT[destination]
            line.append(f"   ·   {count} {label}", style=colour)
        if self.duplicates:
            owners = len(self._copies)
            line.append(f"   ·   {self.duplicates} duplicates", style="#ffb454")
            line.append(f" of {owners} file" + ("" if owners == 1 else "s"), style="#5a6b85")
        if self.with_vins and self.rows:
            confirmed = sum(1 for row in self.rows if (row.get("vin") or {}).get("decoded"))
            candidates = sum(1 for row in self.rows if row.get("vin"))
            line.append(f"   ·   {candidates} VIN candidates", style="#5a6b85")
            line.append(f" ({confirmed} confirmed)", style="#4ad6a8" if confirmed else "#5a6b85")
        if self.stopped:
            line.append("   ·   stopped early", style="#ffb454")
        self.query_one("#batch-summary", Static).update(line)

    def _paint_plan(self) -> None:
        """The second step: where the files would go, before anything moves."""
        widget = self.query_one("#batch-plan", Static)
        if self.filed:
            text = Text()
            text.append("  ✓ FILED  ", style="bold #4ad6a8")
            verb = "moved" if self.filed.get("moved") else "copied"
            text.append(f"{self.filed.get('total')} files {verb}", style="#c8d1e0")
            if self.filed.get("renamed"):
                text.append(f" · {self.filed['renamed']} renamed to avoid collisions", style="#5a6b85")
            if self.filed.get("failed"):
                text.append(f" · {len(self.filed['failed'])} failed", style="bold #ff6b6b")
            text.append("\n")
            for folder, count in list((self.filed.get("folders") or {}).items())[:4]:
                short = self._relative(folder)
                text.append(f"     {count:>4}  {short}\n", style="#5a6b85")
            widget.update(text)
            return
        if not self.plan:
            widget.update(Text(""))
            return
        text = Text()
        text.append("  ORGANIZE — preview, nothing has been touched\n", style="#8aa0c0")
        text.append(f"     {self.plan.get('count')} files would go to:\n", style="#c8d1e0")
        for folder, count in list((self.plan.get("folders") or {}).items())[:5]:
            text.append(f"       {count:>4}  {folder}\n", style="#5a6b85")
        extra = len(self.plan.get("folders") or {}) - 5
        if extra > 0:
            text.append(f"       … {extra} more folders\n", style="#3a4557")
        widget.update(text)

    def _relative(self, folder: str) -> str:
        try:
            return str(Path(folder).relative_to(Path(self.folder or ""))) + "/"
        except ValueError:
            return folder

    def _paint_hints(self) -> None:
        """Keys only, wrapped: this screen has seven of them and a terminal is
        78 columns wide in the case we design for."""
        pairs: list[tuple[str, str, str]] = [("F", " folder", "#4ad6a8")]
        if self.folder:
            pairs.append(("D", " top only" if self.recursive else " +subfolders", "#4ad6a8"))
            pairs.append(("V", " VINs" if not self.with_vins else " no VINs", "#4ad6a8"))
        if self.folder and self.candidates and not self.running and not self.rows:
            pairs.append(("Enter", " scan", "#4ad6a8"))
        if self.running:
            pairs.append(("Esc", " stop (keeps the rows)", "#ffb454"))
        elif self.rows:
            pairs.append(("I", " identify", "#4ad6a8"))
            if self.plan and not self.filed:
                pairs.append(("C", " copy", "#4ad6a8"))
                pairs.append(("M", " move", "#ffb454"))
            elif not self.plan and not self.filed:
                pairs.append(("O", " organize…", "#4ad6a8"))
        if not self.running:
            pairs.append(("Esc", " back", "#4ad6a8"))

        lines: list[Text] = []
        current = Text("  ")
        width = 2
        for key, label, colour in pairs:
            chunk = len(key) + len(label) + 2 + 5  # brackets + gap
            if current.plain.strip() and width + chunk > 74:
                lines.append(current)
                current = Text("  ")
                width = 2
            current.append(f"[{key}]", style=colour)
            current.append(label, style="#3a4557")
            current.append("    ", style="#1e2a38")
            width += chunk
        lines.append(current)
        self.query_one("#batch-hints", Static).update(Text("\n").join(lines))

    # -- work ------------------------------------------------------------
    def action_pick_folder(self) -> None:
        def _chosen(path) -> None:
            if path is None or not Path(path).is_dir():
                return
            self.folder = str(path)
            self.app.note_recent(self.folder, "folder")
            self.rows = []
            self._display = []
            self.candidates = []
            self.plan = None
            self.filed = None
            self.stopped = False
            self._load_candidates()
            self._paint()

        start = Path(self.folder) if self.folder else None
        self.app.push_screen(
            FilePicker(
                start,
                title="Choose a folder of dumps",
                suffixes=BINARY_SUFFIXES,
                folders=True,
            ),
            _chosen,
        )

    def _load_candidates(self) -> None:
        from openremap.api import call

        def listing(recursive: bool) -> dict:
            try:
                return call(
                    "scan_candidates",
                    {"directory": self.folder, "recursive": recursive},
                )
            except Exception:  # noqa: BLE001 - an unreadable folder is a state
                return {"files": [], "skipped": 0}

        current = listing(self.recursive)
        self.candidates = [Path(p) for p in current.get("files") or []]
        self.skipped = int(current.get("skipped") or 0)
        # Only pay for the second walk when it can change the answer.
        self.subfolders = (
            0 if self.recursive or self.candidates else int(listing(True).get("count") or 0)
        )
        self._paint_summary()

    def action_toggle_vins(self) -> None:
        """Read VIN candidates too — on the rows we already have, or next time."""
        self.with_vins = not self.with_vins
        if not self.with_vins:
            self._paint()
            self.app.notify("VIN column will stay empty", timeout=3)
            return
        if not self.rows:
            self._paint()
            self.app.notify("VINs will be read during the scan", timeout=3)
            return
        self.run_worker(self._fill_vins(), name="batch-vins", exclusive=True)

    async def _fill_vins(self) -> None:
        """Read VINs for the rows already on screen, without reclassifying them."""
        import asyncio

        from openremap.api import call

        activity = self.query_one("#batch-activity", ActivityBar)
        table = self.query_one("#batch-table", DataTable)
        total = len(self.rows)
        for position, index in enumerate(self._display or range(total), 1):
            row = self.rows[index]
            activity.start(f"reading VINs · {position}/{total}")
            await self._read_vin(row, call, asyncio)
            if 0 <= position - 1 < table.row_count:
                table.update_cell_at((position - 1, 5), self._vin_cell(row))
        activity.stop(f"VINs read for {total} files")
        self._paint_summary()

    def action_toggle_subfolders(self) -> None:
        """Scan the whole tree instead of just this level (and back)."""
        if not self.folder:
            self.app.notify("choose a folder first — press F", timeout=3)
            return
        self.recursive = not self.recursive
        self.rows = []
        self._display = []
        self.plan = None
        self.filed = None
        self.stopped = False
        self.query_one("#batch-table", DataTable).clear()
        self._load_candidates()
        self._paint()
        if not self.candidates and not self.subfolders:
            self.app.notify("still nothing the engine can read", timeout=4)

    def action_scan(self) -> None:
        if not self.folder:
            self.app.notify("choose a folder first — press F", timeout=3)
            return
        if not self.candidates:
            self._load_candidates()
        if not self.candidates:
            if self.subfolders:
                self.app.notify(
                    f"nothing at the top level — press [D] to include the "
                    f"{self.subfolders} dumps in subfolders",
                    timeout=5,
                )
                return
            self.app.notify("no readable dumps in that folder", timeout=4)
            return
        self.run_worker(self._scan(), name="batch-scan", exclusive=True)

    async def _scan(self) -> None:
        import asyncio

        from openremap.api import call

        if not self.candidates:
            self._load_candidates()
        activity = self.query_one("#batch-activity", ActivityBar)
        table = self.query_one("#batch-table", DataTable)
        table.clear()
        self.rows = []
        self.plan = None
        self.filed = None
        self.duplicates = 0
        #: md5 → the row that owns it, and how many copies exist for each owner.
        #: Kept so a duplicate can name the file it copies ("of #12") and the
        #: original can say how many copies it has.
        self._owners = {}
        self._copies = {}
        self._display = []
        self.stopped = False
        self.running = True
        self._paint_hints()

        total = len(self.candidates)
        started = time.perf_counter()
        seen_hashes: dict[str, str] = {}
        for index, path in enumerate(self.candidates, 1):
            if self.stopped:
                break
            elapsed = time.perf_counter() - started
            pace = elapsed / max(1, index - 1) if index > 1 else 0.0
            eta = f" · ≈{pace * (total - index + 1):.0f} s left" if pace else ""
            activity.start(f"{len(self.rows)}/{total} done · {self._short_name(path)}{eta}")
            try:
                row = await asyncio.to_thread(call, "classify_file", {"path": str(path)})
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                row = {
                    "file": str(path),
                    "destination": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            if self.with_vins:
                await self._read_vin(row, call, asyncio)
            self.rows.append(row)
            digest = str(row.get("md5") or "")
            if digest:
                if digest in self._owners:
                    owner = self._owners[digest]
                    self.duplicates += 1
                    row["duplicate_of"] = owner           # the display row it copies
                    row["duplicate_of_name"] = seen_hashes[digest]
                    self._copies[owner] = self._copies.get(owner, 0) + 1
                else:
                    self._owners[digest] = len(self.rows)
                    seen_hashes[digest] = Path(str(row["file"])).name
            self._add_row(index, row)
            if row.get("duplicate_of"):
                table = self.query_one("#batch-table", DataTable)
                owner = int(row["duplicate_of"])
                copies = self._copies.get(owner, 0)
                if 0 <= owner - 1 < table.row_count:
                    table.update_cell_at((owner - 1, 6), _copies_cell(copies))
            self._paint_summary()

        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.running = False
        pace = self.elapsed_ms / max(1, len(self.rows))
        activity.stop(
            f"{len(self.rows)} files in {self.elapsed_ms / 1000:.1f} s ({pace:.0f} ms/file)"
            + (" · stopped early" if self.stopped else "")
        )
        self._display = list(range(len(self.rows)))
        if self.duplicates and not self.stopped:
            self._regroup()
        self._paint_folder()
        self._paint_summary()
        self._paint_hints()

    def _regroup(self) -> None:
        """Order the table so copies follow the file they copy.

        Scanning streams alphabetically, which scatters a duplicate's copies
        across a thousand rows. Grouping them here answers "which with which"
        without adding a key: the original stays where it was and its copies sit
        underneath, each naming it.
        """
        groups: dict[int, list[int]] = {}
        for index, row in enumerate(self.rows):
            owner = int(row["duplicate_of"]) if row.get("duplicate_of") else index + 1
            groups.setdefault(owner, []).append(index)
        order: list[int] = [index for group in groups.values() for index in group]
        self._display = order

        table = self.query_one("#batch-table", DataTable)
        table.clear()
        for new_index, index in enumerate(order, 1):
            row = dict(self.rows[index])
            if row.get("duplicate_of"):
                row["_is_copy"] = True
                row["_copies"] = 0
            else:
                row["_copies"] = self._copies.get(index + 1, 0)
            row["_display"] = new_index
            self._add_row(new_index, row, regrouped=True)

    @staticmethod
    async def _read_vin(row: dict[str, Any], call, asyncio) -> None:
        """Attach the best VIN candidate to *row* (never fatal)."""
        try:
            report = await asyncio.to_thread(call, "scan_vins", {"path": row["file"]})
        except Exception:  # noqa: BLE001 - a VIN is a bonus, not a requirement
            return
        candidates = list(report.get("candidates") or [])
        if not candidates:
            return
        decoded = [c for c in candidates if c.get("decoded")]
        row["vin"] = max(decoded or candidates, key=lambda c: float(c.get("confidence") or 0))

    def _add_row(
        self, index: int, row: dict[str, Any], *, regrouped: bool = False
    ) -> None:
        table = self.query_one("#batch-table", DataTable)
        name = Path(str(row.get("file"))).name
        size = int(row.get("file_size") or 0)
        size_label = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"
        family = " ".join(
            part
            for part in (row.get("manufacturer"), row.get("ecu_family"))
            if part
        ) or "—"
        software = str(row.get("match_key") or row.get("software_version") or "—").split("::")[-1]
        label = verdict_label(str(row.get("destination")))
        # "Which with which", drawn instead of numbered: copies sit under their
        # original (see ``_regroup``) with an arrow, and the original carries the
        # count. Nothing is printed for the thousands of files that are unique.
        is_copy = bool(row.get("duplicate_of")) or bool(row.get("_is_copy"))
        if is_copy:
            dup_cell: Any = Text("copy", style="#5a6b85")
        elif regrouped:
            dup_cell = _copies_cell(int(row.get("_copies") or 0))
        else:
            dup_cell = _copies_cell(self._copies.get(len(self.rows), 0))
        name_cell: Any = name[:44]
        if is_copy:
            name_cell = Text("↳ ", style="#5a6b85")
            name_cell.append(f"{name}"[:42], style="#8aa0c0")
        table.add_row(
            str(index),
            name_cell,
            size_label,
            family[:20],
            software[:20],
            self._vin_cell(row),
            dup_cell,
            label,
        )

    @staticmethod
    def _vin_cell(row: dict[str, Any]) -> Any:
        """A confirmed VIN, or the confidence of an unconfirmed candidate.

        ``scan_vins`` reports 17-character *candidates*; only a candidate whose
        check digit decodes is a VIN. Printing a candidate as if it were a VIN
        would be a lie with a 17-character alibi, so the two cases look different:
        a decoded VIN is text, a candidate is a number with a tilde.
        """
        vin = row.get("vin") or {}
        if not vin:
            return ""
        if vin.get("decoded") and vin.get("vin"):
            return Text(str(vin["vin"]), style="#4ad6a8")
        confidence = float(vin.get("confidence") or 0.0)
        return Text(f"~{confidence:.2f}", style="#5a6b85")

    @staticmethod
    def _short_name(path: Path) -> str:
        name = path.name
        return name if len(name) <= 38 else f"{name[:18]}…{name[-18:]}"

    # -- second step: organising -----------------------------------------
    def action_organize(self) -> None:
        if not self.rows:
            self.app.notify("scan the folder first", timeout=3)
            return
        from openremap.api import call

        try:
            self.plan = call(
                "organize_preview", {"directory": self.folder, "rows": self.rows}
            )
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"could not plan: {type(exc).__name__}: {exc}", timeout=5)
            return
        self._paint_plan()
        self._paint_hints()

    def action_copy_files(self) -> None:
        self._file(move=False)

    def action_move_files(self) -> None:
        self._file(move=True)

    def _file(self, *, move: bool) -> None:
        if not self.plan:
            self.app.notify("press [O] to preview where the files go", timeout=3)
            return
        from openremap.api import call

        try:
            self.filed = call(
                "organize_apply",
                {"directory": self.folder, "rows": self.rows, "move": move},
            )
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"filing failed: {type(exc).__name__}: {exc}", timeout=5)
            return
        self.plan = None
        self._paint_plan()
        self._paint_hints()
        verb = "moved" if move else "copied"
        self.app.notify(f"{self.filed.get('total')} files {verb}", timeout=4)

    # -- actions ---------------------------------------------------------
    def action_open_selected(self) -> None:
        table = self.query_one("#batch-table", DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(self.rows):
            return
        cursor = table.cursor_row
        if not (0 <= cursor < len(self._display)):
            return
        target = str(self.rows[self._display[cursor]].get("file") or "")
        if not target:
            return
        from openremap.tui.screens.identify import IdentifyScreen

        self.app.push_screen(IdentifyScreen(target))

    def action_back(self) -> None:
        if self.running:
            # Stop the loop and keep what is already classified.
            self.stopped = True
            self.app.notify("stopping after this file — results kept", timeout=3)
            return
        self.app.pop_screen()
