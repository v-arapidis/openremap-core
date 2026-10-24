"""
The CONTRIBUTE screen — measure your own dump, then hand over the files.

Adding a family is a pull request, and the tool's promise about it is narrow and
worth stating plainly on screen: **your dumps never leave the machine**. The
wizard measures them and renders a *fixture* (a synthetic binary carrying only
the marker), an extractor skeleton, a test and a report. That is what gets
committed.

Two steps, and the safe one is the default:

* **measure** (`Enter`) — pure local computation: who claims each dump, which
  byte runs are stable across them, the candidate identity fields. No collision
  check, no recommendation, nothing written.
* **prepare** (`P`) — needs a brand and a family, because the artifacts have to
  declare what the extractor is for. It runs the collision check against every
  known family's fixture, renders the four artifacts, and runs the review bar
  over the rendered source. Until this step, "no collisions" has not been
  tested — and the screen says so instead of implying otherwise.

Artifacts are written **into the workspace**, never into a source tree the tool
did not create, and never the dumps themselves.
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
from openremap.tui.screens.picker import BINARY_SUFFIXES, FilePicker
from openremap.tui.widgets.activity import ActivityBar
from openremap.tui.widgets.prompt import PromptScreen

__all__ = ["ContributeScreen"]

#: Artifact key → the name it gets inside the contribution folder.
_ARTIFACTS = (
    ("extractor", "extractor.py"),
    ("test", "test_extractor.py"),
    ("report", "contribution.md"),
    ("recipe", "fixture.json"),
)


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


class ContributeScreen(Screen):
    """Measure dumps for a new family, then render a contribution."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "add_dump", "Add dump", show=True),
        Binding("b", "brand", "Brand", show=True),
        Binding("t", "family", "Family", show=True),
        Binding("enter", "measure", "Measure", show=True),
        # Contextual, advertised only once a brand and family are set.
        Binding("p", "prepare", "", show=False),
        Binding("w", "write", "", show=False),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, dumps: list[str] | None = None) -> None:
        super().__init__()
        self.dumps: list[str] = list(dumps or [])
        self.brand: str | None = None
        self.family: str | None = None
        self.measurement: dict[str, Any] | None = None
        self.prepared: dict[str, Any] | None = None
        self.written: dict[str, Path] = {}
        self.elapsed_ms = 0.0
        self.busy = False

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="contribute-panel"):
            yield Static("", id="contribute-title")
            yield Static("", id="contribute-dumps")
            yield Static("", id="contribute-names")
            yield ActivityBar(id="contribute-activity")
            yield Static("", id="contribute-body")
            yield Static("", id="contribute-hints")

    def on_mount(self) -> None:
        self.query_one("#contribute-title", Static).update(
            Text(
                "CONTRIBUTE — measure your own dump for a family the engine does not know",
                style="#5a6b85",
            )
        )
        self._paint()

    # -- painting -------------------------------------------------------
    def _paint(self) -> None:
        self._paint_inputs()
        self._paint_body()
        self._paint_hints()

    def _paint_inputs(self) -> None:
        dumps = Text()
        dumps.append("  dumps   ", style="#3a4557")
        dumps.append("[F]", style="#4ad6a8")
        dumps.append("  ", style="#1e2a38")
        if not self.dumps:
            dumps.append("add dumps from different calibrations of one family", style="#5a6b85")
        else:
            for index, path in enumerate(self.dumps, 1):
                dumps.append(f"{index}. {Path(path).name}", style="#c8d1e0")
                dumps.append("   ", style="#1e2a38")
            if len(self.dumps) == 1:
                dumps.append("(one dump cannot prove a marker is stable)", style="#ffb454")
        self.query_one("#contribute-dumps", Static).update(dumps)

        names = Text()
        names.append("  names   ", style="#3a4557")
        names.append("[B]", style="#4ad6a8")
        names.append(" brand  ", style="#1e2a38")
        names.append(self.brand or "—", style="#c8d1e0" if self.brand else "#5a6b85")
        names.append("     ", style="#1e2a38")
        names.append("[T]", style="#4ad6a8")
        names.append(" family  ", style="#1e2a38")
        names.append(self.family or "—", style="#c8d1e0" if self.family else "#5a6b85")
        if not (self.brand and self.family):
            names.append("   (needed to render the contribution, not to measure)", style="#3a4557")
        self.query_one("#contribute-names", Static).update(names)

    def _paint_body(self) -> None:
        body = self.query_one("#contribute-body", Static)
        if self.prepared is not None:
            body.update(self._render_prepared())
            return
        if self.measurement is not None:
            body.update(self._render_measurement())
            return
        text = Text()
        text.append(
            "  Your dumps never leave this machine. What a contribution commits is a\n"
            "  synthetic fixture carrying only the marker, an extractor skeleton, a test\n"
            "  and a report — never your binary.\n\n",
            style="#3a4557",
        )
        if self.dumps:
            text.append("  → press ", style="#5a6b85")
            text.append("[Enter]", style="bold #4ad6a8")
            text.append(" to measure them", style="#5a6b85")
        else:
            text.append("  → press ", style="#5a6b85")
            text.append("[F]", style="bold #4ad6a8")
            text.append(" to add a dump of the family you want added", style="#5a6b85")
        body.update(text)

    def _render_measurement(self) -> Text:
        measurement = self.measurement or {}
        analysis = measurement.get("analysis") or {}
        text = Text()
        text.append("  MEASURED", style="bold #4ad6a8")
        text.append(
            f"   {len(analysis.get('dumps') or [])} dump(s) · {self.elapsed_ms:.0f} ms\n\n",
            style="#8aa0c0",
        )
        diversity = analysis.get("calibration_diversity") or {}
        if diversity:
            # Before the candidates: it decides what they are worth.
            colour = {
                "different-calibrations": "#4ad6a8",
                "same-calibration": "#ffb454",
                "near-identical": "#ffb454",
            }.get(str(diversity.get("verdict")), "#8aa0c0")
            text.append("\n     what these dumps can prove   ", style="#8aa0c0")
            text.append(f"{diversity.get('verdict')}\n", style=colour)
            text.append(f"       {_short(str(diversity.get('note') or ''), 74)}\n",
                        style="#5a6b85")
        for entry in analysis.get("dumps") or []:
            claim = entry.get("claimed_by")
            text.append(f"     {_short(entry.get('name') or '?', 40)}", style="#c8d1e0")
            if claim:
                text.append(f"   already claimed by {claim.get('family') or claim.get('extractor')}\n",
                            style="#ffb454")
            else:
                text.append("   not claimed by any extractor today\n", style="#4ad6a8")
        candidates = analysis.get("candidates") or []
        if candidates:
            text.append(
                "\n     signature candidates (byte runs stable across the dumps you gave)\n",
                style="#8aa0c0",
            )
            for candidate in candidates[:5]:
                marker = str(candidate.get("marker") or "?")
                length = int(candidate.get("marker_len") or len(marker))
                score = int(candidate.get("score") or 0)
                text.append(f"       {length:>3} B  score {score:>3}  ", style="#5a6b85")
                text.append(f"{_short(marker, 52)}\n", style="#c8d1e0")
        notes = analysis.get("notes") or []
        for note in notes[:3]:
            text.append(f"\n     ⚠ {_short(note, 96)}\n", style="#ffb454")
        return text

    def _render_prepared(self) -> Text:
        prepared = self.prepared or {}
        verification = prepared.get("verification") or {}
        spec = prepared.get("spec") or {}
        text = Text()
        ok = bool(verification.get("ok"))
        text.append("  ✓ RENDERED" if ok else "  ✗ RENDERED, REVIEW FAILED",
                    style="bold #4ad6a8" if ok else "bold #ff6b6b")
        text.append(f"   {spec.get('brand')} {spec.get('family')} · {self.elapsed_ms:.0f} ms\n\n",
                    style="#8aa0c0")
        marker = spec.get("marker_hex") or ""
        if marker:
            text.append(f"     MARKER      {_short(marker, 68)}\n", style="#5a6b85")
        for check in verification.get("checks") or []:
            mark = "✓" if check.get("ok") else "✗"
            colour = "#4ad6a8" if check.get("ok") else "#ff6b6b"
            text.append(f"     {mark} {check.get('name')!s:<34}", style=colour)
            text.append(f"{_short(check.get('detail') or '', 34)}\n", style="#5a6b85")
        if self.written:
            text.append("\n     written into the workspace:\n", style="#8aa0c0")
            for key, target in self.written.items():
                text.append(f"       {key:<10} {paths.label(target)}\n", style="#4ad6a8")
        elif prepared.get("artifacts"):
            text.append("\n     → press ", style="#5a6b85")
            text.append("[W]", style="bold #4ad6a8")
            text.append(" to write the artifacts into the workspace", style="#5a6b85")
        return text

    def _paint_hints(self) -> None:
        hints = Text("  ")
        hints.append("[F]", style="#4ad6a8")
        hints.append(" dump    ", style="#3a4557")
        hints.append("[B]", style="#4ad6a8")
        hints.append(" brand    ", style="#3a4557")
        hints.append("[T]", style="#4ad6a8")
        hints.append(" family", style="#3a4557")
        if self.dumps and not self.busy:
            hints.append("    ", style="#1e2a38")
            hints.append("[Enter]", style="#4ad6a8")
            hints.append(" measure", style="#3a4557")
        if self.brand and self.family and self.dumps and not self.busy:
            hints.append("    ", style="#1e2a38")
            hints.append("[P]", style="#4ad6a8")
            hints.append(" render", style="#3a4557")
        if self.prepared and not self.written:
            hints.append("    ", style="#1e2a38")
            hints.append("[W]", style="#4ad6a8")
            hints.append(" write", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" back", style="#3a4557")
        self.query_one("#contribute-hints", Static).update(hints)

    # -- actions ---------------------------------------------------------
    def action_add_dump(self) -> None:
        def _chosen(path) -> None:
            if path is None:
                return
            target = str(path)
            if target in self.dumps:
                self.app.notify("that dump is already in the list", timeout=3)
                return
            self.dumps.append(target)
            self.measurement = None
            self.prepared = None
            self.written = {}
            self._paint()

        start = Path(self.dumps[-1]).parent if self.dumps else None
        self.app.push_screen(
            FilePicker(start, title="Add a dump of the family to add", suffixes=BINARY_SUFFIXES),
            _chosen,
        )

    def action_brand(self) -> None:
        def _chosen(value) -> None:
            if value is None:
                return
            self.brand = value
            self.prepared = None
            self.written = {}
            self._paint()

        self.app.push_screen(
            PromptScreen(
                "Manufacturer name",
                hint="e.g. Siemens — the artifacts declare it, measurement does not need it.",
                value=self.brand or "",
            ),
            _chosen,
        )

    def action_family(self) -> None:
        def _chosen(value) -> None:
            if value is None:
                return
            self.family = value
            self.prepared = None
            self.written = {}
            self._paint()

        self.app.push_screen(
            PromptScreen(
                "Family key",
                hint="e.g. SID206 — how the extractor will name what it recognises.",
                value=self.family or "",
            ),
            _chosen,
        )

    def action_measure(self) -> None:
        if not self.dumps:
            self.app.notify("add a dump first — press F", timeout=3)
            return
        self.run_worker(self._measure(), name="contribute-measure", exclusive=True)

    async def _measure(self) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#contribute-activity", ActivityBar)
        activity.start(f"measuring {len(self.dumps)} dump(s) — local only")
        self.busy = True
        self._paint_hints()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                "contribute_extractor",
                {"paths": list(self.dumps), "measure_only": True},
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            activity.stop()
            self.busy = False
            self._paint_hints()
            self.app.notify(f"measure failed: {type(exc).__name__}: {exc}", timeout=8)
            return
        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.measurement = dict(result)
        self.prepared = None
        self.written = {}
        self.busy = False
        activity.stop(f"measured in {self.elapsed_ms:.0f} ms")
        self._paint()

    def action_prepare(self) -> None:
        if not (self.brand and self.family):
            self.app.notify("set a brand and a family first — press B and T", timeout=4)
            return
        if not self.dumps:
            self.app.notify("add a dump first — press F", timeout=3)
            return
        self.run_worker(self._prepare(), name="contribute-prepare", exclusive=True)

    async def _prepare(self) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#contribute-activity", ActivityBar)
        activity.start(f"rendering {self.brand} {self.family} — collision check + review bar")
        self.busy = True
        self._paint_hints()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                "contribute_extractor",
                {
                    "paths": list(self.dumps),
                    "brand": self.brand,
                    "family": self.family,
                },
            )
        except Exception as exc:  # noqa: BLE001
            activity.stop()
            self.busy = False
            self._paint_hints()
            self.app.notify(f"could not render: {type(exc).__name__}: {exc}", timeout=10)
            return
        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.prepared = dict(result)
        self.written = {}
        self.busy = False
        activity.stop(f"rendered in {self.elapsed_ms:.0f} ms")
        self._paint()

    def action_write(self) -> None:
        """Write the rendered artifacts into the workspace — never the dumps."""
        artifacts = (self.prepared or {}).get("artifacts") or {}
        if not artifacts:
            self.app.notify("render the contribution first — press P", timeout=3)
            return
        folder = paths.subdir("contributions") / f"{self.brand}-{self.family}"
        written: dict[str, Path] = {}
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for key, filename in _ARTIFACTS:
                payload = artifacts.get(key)
                if not payload:
                    continue
                target = folder / filename
                if isinstance(payload, str):
                    target.write_text(payload, encoding="utf-8")
                else:
                    import json

                    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                written[key] = target
        except OSError as exc:
            self.app.notify(f"could not write: {exc}", timeout=6)
            return
        self.written = written
        self._paint()
        self.app.notify(f"{len(written)} files written to {paths.label(folder)}", timeout=5)

    def action_back(self) -> None:
        self.app.pop_screen()
