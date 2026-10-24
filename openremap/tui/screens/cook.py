"""
The COOK screen — two files in, one recipe out.

The shape of the job is two inputs, one *consequence*, and a report:

* **two inputs** — the stock file and the edited file. Cheap facts (size, and
  whether they are literally the same path) are shown immediately; nothing else
  is guessed before the engine speaks.
* **one consequence** — a recipe built from unique anchors applies to *any* dump
  of the family; a recipe forced past repeated anchors applies only to the same
  firmware. So the screen does not ask "strict or forced?" as if it were a
  preference: it runs the safe cook, and only when the engine refuses *because*
  of repeated anchors does it offer the looser rung, with what that costs
  written on the key itself. The mode is a property of the pair, not a taste.
* **a report** — counts, regions, flags (information, never a filter), the
  contract, the fingerprint, and then the save, at a path shown *before* the
  key is pressed.

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

__all__ = ["CookScreen"]

#: Plain-language meaning of each refusal rule — what to do about it.
_RULE_HEADLINE = {
    "size": "the two files are different sizes",
    "identity": "different firmware",
    "empty": "the two files are identical",
    "unique": "some anchors are not unique",
    "identity_required": "not recognised, and the anchors repeat",
}

#: What the reader can *do* about each refusal. The engine's own ``next_step``
#: is for the CLI (which has no keys to press); here the fix is a key, so the
#: screen says the fix in its own words instead of repeating the sentence.
_RULE_ADVICE = {
    "size": "pick two copies of the same image ([F] / [T])",
    "identity": "pick a stock/tuned pair of the same firmware ([F] / [T])",
    "empty": "pick the edited file — this pair has no changes ([T])",
    "identity_required": "a recipe like this is only usable where the identity verifies",
}

#: One line of plain English per instruction flag kind — flags never drop bytes.
_FLAG_MEANING = {
    "CODE_AREA": "touch code/shared areas",
    "LOW_ENTROPY_CTX": "in low-entropy context",
    "WEAK_ANCHOR": "with unproven anchors",
    "ERASED": "in erased flash",
}


def _short(name: str, limit: int = 34) -> str:
    """Middle-out shortening, so a long dump name still fits one line."""
    if len(name) <= limit:
        return name
    keep = limit - 1
    head = keep // 2
    return f"{name[:head]}…{name[-(keep - head):]}"


def _size_label(size: int) -> str:
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"


class CookScreen(Screen):
    """Build a schema-5.0 recipe from a stock/tuned pair."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f", "pick_stock", "Stock file", show=True),
        Binding("t", "pick_tuned", "Tuned file", show=True),
        Binding("enter", "cook", "Cook", show=True),
        # Contextual: advertised in the hint line only in the state that allows it.
        Binding("m", "cook_anyway", "", show=False),
        Binding("s", "save", "", show=False),
        Binding("d", "diff_pair", "", show=False),
        Binding("v", "verify", "", show=False),
        Binding("a", "apply_recipe", "", show=False),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, stock: str | None = None, tuned: str | None = None) -> None:
        super().__init__()
        self.stock = stock
        self.tuned = tuned
        self.report: dict[str, Any] | None = None
        self.recipe: dict[str, Any] | None = None
        self.saved_to: Path | None = None
        self.cook_ms = 0.0
        self._notice: Text | None = None
        self._overwrite_armed = False
        #: match_key of each side, read in the background as soon as both files
        #: are known — the same fact cook itself checks, shown before the run.
        self.identity: dict[str, str | None] = {}
        self._identifying = False
        #: The audit of stock + tuned + recipe: the triplet's own proof.
        self.verification: dict[str, Any] | None = None
        self.verify_ms = 0.0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="cook-panel"):
            yield Static("", id="cook-title")
            yield Static("", id="cook-stock")
            yield Static("", id="cook-tuned")
            yield Static("", id="cook-basis")
            yield ActivityBar(id="cook-activity")
            yield Static("", id="cook-body")
            yield Static("", id="cook-hints")

    def on_mount(self) -> None:
        self.query_one("#cook-title", Static).update(
            Text("COOK — build a recipe from a stock file and its edited version", style="#5a6b85")
        )
        if self.can_cook:
            self._identify_pair()
        else:
            self._paint()

    # -- state helpers --------------------------------------------------
    @property
    def can_cook(self) -> bool:
        return bool(self.stock and self.tuned)

    def _file_line(self, label: str, key: str, value: str | None, style: str) -> Text:
        line = Text()
        line.append(f"  {label:<7}", style="#3a4557")
        line.append(f"[{key}]", style="#4ad6a8")
        line.append("  ", style="#1e2a38")
        if not value:
            line.append("choose a file", style="#5a6b85")
            return line
        path = Path(value)
        line.append(_short(path.name, 40), style=style)
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

    # -- painting -------------------------------------------------------
    def _paint(self) -> None:
        self.query_one("#cook-stock", Static).update(
            self._file_line("stock", "F", self.stock, "#c8d1e0")
        )
        self.query_one("#cook-tuned", Static).update(
            self._file_line("tuned", "T", self.tuned, "#c8d1e0")
        )
        self._paint_basis()
        self._paint_body()
        self._paint_hints()

    def _paint_basis(self) -> None:
        """What is known *before* cooking: sizes, and the mode that will be used."""
        status = self.query_one("#cook-basis", Static)
        if not self.can_cook:
            status.update(
                Text(
                    "  Both files are the same ECU binary — one before the edit, one after.",
                    style="#3a4557",
                )
            )
            return
        line = Text()
        stock, tuned = Path(self.stock or ""), Path(self.tuned or "")
        same_path = stock.resolve() == tuned.resolve() if stock.exists() and tuned.exists() else False
        if same_path:
            line.append("  ✗ the same file is selected twice", style="#ffb454")
            line.append(" — there is nothing to diff", style="#3a4557")
        else:
            try:
                stock_size, tuned_size = stock.stat().st_size, tuned.stat().st_size
            except OSError:
                stock_size = tuned_size = -1
            if stock_size != tuned_size:
                line.append("  ✗ different sizes", style="#ffb454")
                line.append(
                    f" — {_size_label(stock_size)} vs {_size_label(tuned_size)}; "
                    "a recipe needs the same image",
                    style="#3a4557",
                )
            else:
                line.append("  ✓ same size", style="#4ad6a8")
                stock_key, tuned_key = self.identity.get("stock"), self.identity.get("tuned")
                if stock_key and tuned_key and stock_key != tuned_key:
                    line.append("   ·   ", style="#1e2a38")
                    line.append("✗ different firmware", style="#ffb454")
                    line.append(" — cook will refuse this pair", style="#3a4557")
                elif stock_key and tuned_key:
                    line.append("   ·   ", style="#1e2a38")
                    line.append("✓ same firmware", style="#4ad6a8")
                    line.append(f"   {stock_key}", style="#5a6b85")
                else:
                    line.append(
                        "   ·   strict cook: unique anchors → any dump of this family",
                        style="#3a4557",
                    )
        status.update(line)

    def _paint_body(self) -> None:
        body = self.query_one("#cook-body", Static)
        report = self.report
        if report is None:
            if self._notice is not None:
                body.update(self._notice)
                return
            text = Text()
            text.append(
                "  A recipe records what changed between the two files, and how to find\n"
                "  those changes again in another copy. Nothing is dropped: every changed\n"
                "  byte is in it, and suspicious ones are flagged, not removed.\n\n",
                style="#3a4557",
            )
            if self.can_cook:
                text.append("  → press ", style="#5a6b85")
                text.append("[Enter]", style="bold #4ad6a8")
                text.append(" to cook this pair", style="#5a6b85")
                text.append("   (the mode follows from the files: nothing to choose)", style="#3a4557")
            else:
                missing = "stock" if not self.stock else "tuned"
                text.append("  → press ", style="#5a6b85")
                text.append("[F]" if missing == "stock" else "[T]", style="bold #4ad6a8")
                text.append(f" to choose the {missing} file", style="#5a6b85")
            body.update(text)
            return

        text = Text()
        if report.get("refusal"):
            self._paint_refusal(text, report)
        elif report.get("result", {}).get("produced"):
            self._paint_success(text, report)
        body.update(text)

    def _paint_refusal(self, text: Text, report: dict[str, Any]) -> None:
        refusal = report["refusal"]
        rule = str(refusal.get("rule") or "?")
        text.append("  ✗ NOT COOKED", style="bold #ff6b6b")
        text.append(f" — {_RULE_HEADLINE.get(rule, rule)}\n\n", style="#ff8a8a")
        identity = report.get("identity") or {}
        if rule == "identity":
            text.append(f"     stock   {identity.get('stock_match_key')}\n", style="#8aa0c0")
            text.append(f"     tuned   {identity.get('tuned_match_key')}\n", style="#8aa0c0")
        text.append(f"     {refusal.get('message')}\n", style="#c8d1e0")
        advice = _RULE_ADVICE.get(rule)
        if advice:
            text.append(f"\n     → {advice}\n", style="#5a6b85")

    def _paint_success(self, text: Text, report: dict[str, Any]) -> None:
        counts = report.get("counts") or {}
        regions = report.get("regions") or {}
        contract = report.get("contract") or {}
        result = report.get("result") or {}

        text.append("  ✓ RECIPE BUILT", style="bold #4ad6a8")
        text.append(
            f"    {counts.get('instructions')} instructions · "
            f"{int(counts.get('bytes_changed') or 0):,} bytes · "
            f"{counts.get('maps')} maps\n\n",
            style="#8aa0c0",
        )
        by_region = regions.get("by_region") or {}
        spread = " · ".join(f"{name} {count}" for name, count in by_region.items())
        text.append(f"     REGIONS      {spread or '—'}\n", style="#c8d1e0")

        flags = report.get("flags") or {}
        if flags:
            for kind, count in flags.items():
                meaning = _FLAG_MEANING.get(str(kind), "flagged")
                text.append(
                    f"     FLAGS        {count} {_short(meaning, 40)} ({kind})\n",
                    style="#c8d1e0",
                )
                text.append(
                    "                  flagged, not dropped — nothing is excluded\n",
                    style="#5a6b85",
                )
        else:
            text.append("     FLAGS        none\n", style="#c8d1e0")

        floor = contract.get("identity_floor")
        floor_text = (
            "applies to any dump of this family"
            if floor == "any"
            else "applies to this exact firmware only"
            if floor == "firmware"
            else str(floor)
        )
        sections = ", ".join(contract.get("sections") or []) or "none"
        text.append(f"     PORTABILITY  {floor_text}\n", style="#4ad6a8")
        text.append(
            f"                  identity_floor {floor} · anchors_unique "
            f"{'yes' if contract.get('anchors_unique') else 'no'} · sections {sections}\n",
            style="#5a6b85",
        )
        text.append(
            f"     SCHEMA       {result.get('schema')} · {report.get('command')}\n",
            style="#8aa0c0",
        )
        recipe = self.recipe or {}
        for label, block in (("SOURCE", recipe.get("source")), ("TUNE", recipe.get("tune"))):
            digest = str((block or {}).get("sha256") or "")
            name = str((block or {}).get("filename") or "")
            if digest:
                text.append(
                    f"     {label:<12} {digest[:16]}…  {_short(name, 28)}\n", style="#5a6b85"
                )
        fingerprint = str(result.get("fingerprint") or "")
        if fingerprint:
            text.append(f"     FINGERPRINT  {fingerprint[:48]}\n", style="#5a6b85")
        text.append(f"     TIMING       {self.cook_ms:.0f} ms\n", style="#3a4557")
        # Once the recipe exists on disk the next step is a key, not a command
        # line; until then the only honest pointer is the CLI.
        if self.saved_to:
            text.append(
                f"     NEXT         [A] apply this recipe    "
                f"(or: openremap tune-strict <target.bin> {_short(self.saved_to.name, 28)})\n",
                style="#3a4557",
            )
        else:
            text.append(
                "     NEXT         openremap tune-strict <target.bin> <recipe>"
                "   (or [S] save, then [A])\n",
                style="#3a4557",
            )
        if self.verification is not None:
            audit = self.verification
            verdict = "✓ VERIFIED" if audit.get("clean") else "✗ NOT VERIFIED"
            style = "bold #4ad6a8" if audit.get("clean") else "bold #ff6b6b"
            text.append(f"\n     {verdict}", style=style)
            text.append(
                " — applying this recipe to the stock reproduces the tuned file\n"
                if audit.get("clean")
                else " — the three files do not agree\n",
                style="#8aa0c0",
            )
            provenance = audit.get("provenance") or {}
            text.append(
                f"                  recipe was cooked from this stock: "
                f"{'yes' if provenance.get('ok') else 'no'}\n",
                style="#5a6b85",
            )
            unaccounted = audit.get("unaccounted") or {}
            text.append(
                f"                  changed bytes with no instruction: "
                f"{int(unaccounted.get('bytes') or 0):,}\n",
                style="#5a6b85",
            )
            for warning in (audit.get("warnings") or [])[:3]:
                text.append(f"                  ⚠ {_short(str(warning), 56)}\n", style="#ffb454")
            text.append(f"                  verified in {self.verify_ms:.0f} ms\n", style="#3a4557")

        # The save state is *not* part of the report: the report describes the
        # recipe, the status line below describes the file. Keeping them apart
        # means the warning cannot get lost inside a block of measurements.

    def _save_status(self) -> Text:
        """The loud line: a recipe that exists only in memory is easy to lose."""
        line = Text()
        report = self.report or {}
        produced = bool(report.get("result", {}).get("produced"))
        if not produced:
            return line
        target = self._save_target()
        label = paths.label(target)  # ~/Documents/OpenRemap/recipes/<name>.remap
        # Keep the whole line inside 78 columns: the prefix eats the budget, so
        # the path is shortened to whatever is left.
        if self.saved_to and not self._overwrite_armed:
            prefix = "  ✓ SAVED  "
            line.append(prefix, style="bold #4ad6a8")
            line.append(_short(label, 78 - len(prefix)), style="#4ad6a8")
        elif self._overwrite_armed:
            prefix = "  ⚠ EXISTS — press [S] again to overwrite"
            line.append(prefix, style="bold #ffb454")
            line.append(f"  {_short(label, 78 - len(prefix) - 2)}", style="#ffb454")
        else:
            prefix = "  ⚠ NOT SAVED — press [S] to write"
            line.append(prefix, style="bold #ffb454")
            line.append(f" {_short(label, 78 - len(prefix) - 1)}", style="bold #ffb454")
        return line

    def _paint_hints(self) -> None:
        """Only the keys that do something in this state."""
        hints = Text("  ")
        hints.append("[F]", style="#4ad6a8")
        hints.append(" stock file    ", style="#3a4557")
        hints.append("[T]", style="#4ad6a8")
        hints.append(" tuned file", style="#3a4557")
        report = self.report or {}
        produced = bool(report.get("result", {}).get("produced"))
        if self.can_cook and not produced:
            hints.append("    ", style="#1e2a38")
            hints.append("[Enter]", style="#4ad6a8")
            hints.append(" cook", style="#3a4557")
            # The other question you can ask of this exact pair. Offered before the
            # cook, because that is when looking at the change is useful.
            hints.append("   ", style="#1e2a38")
            hints.append("[D]", style="#4ad6a8")
            hints.append(" diff this pair", style="#3a4557")
        if report.get("refusal") and report["refusal"].get("rule") == "unique":
            hints.append("    ", style="#1e2a38")
            hints.append("[M]", style="#ffb454")
            hints.append(" cook anyway (needs the same firmware to apply)", style="#3a4557")
        if produced and self.verification is None:
            hints.append("    ", style="#1e2a38")
            hints.append("[V]", style="#4ad6a8")
            hints.append(" verify the three files", style="#3a4557")
        if produced and self.saved_to:
            hints.append("    ", style="#1e2a38")
            hints.append("[A]", style="#4ad6a8")
            hints.append(" apply this recipe", style="#3a4557")
        elif produced:
            hints.append("    ", style="#1e2a38")
            hints.append("[S]", style="#4ad6a8")
            hints.append(" overwrite" if self._overwrite_armed else " save", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" back", style="#3a4557")
        status = self._save_status()
        footer = Text()
        footer.append_text(status)
        footer.append("\n")
        footer.append_text(hints)
        self.query_one("#cook-hints", Static).update(footer)

    def _save_target(self) -> Path:
        tuned = Path(self.tuned or "recipe")
        return paths.subdir("recipes") / f"{tuned.stem}.remap"

    # -- work -----------------------------------------------------------
    def action_pick_stock(self) -> None:
        def _chosen(path) -> None:
            if path is not None:
                self.stock = str(path)
                self._reset_result()
                self._identify_pair()

        start = Path(self.stock).parent if self.stock else None
        self.app.push_screen(FilePicker(start), _chosen)

    def action_pick_tuned(self) -> None:
        def _chosen(path) -> None:
            if path is not None:
                self.tuned = str(path)
                self._reset_result()
                self._identify_pair()

        start = Path(self.tuned).parent if self.tuned else None
        self.app.push_screen(FilePicker(start), _chosen)

    def _identify_pair(self) -> None:
        """Read both match keys in the background — the fact cook itself checks."""
        self.identity = {}
        if not self.can_cook:
            self._paint()
            return
        self._identifying = True
        self.run_worker(self._read_identities(), name="cook-identify", exclusive=True)

    async def _read_identities(self) -> None:
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

    def _reset_result(self) -> None:
        self.verification = None
        self.report = None
        self.recipe = None
        self.saved_to = None
        self._notice = None
        self._overwrite_armed = False

    def action_cook(self) -> None:
        """Cook the pair with the strict mode — the safe rung of the ladder."""
        self._cook("cook_strict")

    def action_cook_anyway(self) -> None:
        """The looser rung, offered only after the engine refused for anchors."""
        rule = ((self.report or {}).get("refusal") or {}).get("rule")
        if rule != "unique":
            self.app.notify("only offered when the anchors repeat", timeout=3)
            return
        self._cook("cook_forced")

    def _cook(self, method: str) -> None:
        if not self.can_cook:
            self.app.notify("choose both files first", timeout=3)
            return
        self._reset_result()
        self.run_worker(self._run(method), name="cook", exclusive=True)

    async def _run(self, method: str) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#cook-activity", ActivityBar)
        size = 0
        try:
            size = Path(self.tuned or "").stat().st_size
        except OSError:
            pass
        activity.start(f"cooking {_size_label(size)} → changes, regions, maps")
        self._paint_hints()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                method,
                {"stock_path": self.stock, "tuned_path": self.tuned},
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            activity.stop()
            self._notice = Text(f"  cook failed: {type(exc).__name__}: {exc}", style="bold #ff6b6b")
            self._paint_basis()
            return

        self.cook_ms = (time.perf_counter() - started) * 1000
        self.report = dict(result.get("report") or {})
        if self.report.get("result", {}).get("produced"):
            self.app.note_recent(self.stock, "dump")
            self.app.note_recent(self.tuned, "dump")
        self.recipe = result.get("recipe")
        activity.stop(f"cooked in {self.cook_ms:.0f} ms")
        self._paint()

    def action_diff_pair(self) -> None:
        """Hand the same pair to DIFF — one pick, two questions."""
        if not self.can_cook:
            self.app.notify("choose both files first", timeout=3)
            return
        from openremap.tui.screens.diff import DiffScreen

        self.app.push_screen(DiffScreen(stock=self.stock, tuned=self.tuned))

    def action_apply_recipe(self) -> None:
        """Hand the recipe to APPLY — the step this screen exists to feed."""
        if not self.saved_to:
            self.app.notify("save the recipe first — press S", timeout=4)
            return
        from openremap.tui.screens.apply import ApplyScreen

        self.app.push_screen(ApplyScreen(recipe=str(self.saved_to)))

    def action_verify(self) -> None:
        """Audit stock + tuned + recipe: does the triplet actually agree?"""
        if not self.recipe:
            self.app.notify("cook a recipe first", timeout=3)
            return
        if not (self.stock and self.tuned):
            self.app.notify("both files are needed to verify", timeout=3)
            return
        self.run_worker(self._verify(), name="cook-verify", exclusive=True)

    async def _verify(self) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#cook-activity", ActivityBar)
        activity.start("verifying: cook the recipe again from the pair and compare")
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                "audit",
                {
                    "stock_path": self.stock,
                    "tuned_path": self.tuned,
                    "recipe": self.recipe,
                    "recipe_name": "recipe.remap",
                },
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            activity.stop()
            self.app.notify(f"verify failed: {type(exc).__name__}: {exc}", timeout=6)
            return
        self.verify_ms = (time.perf_counter() - started) * 1000
        self.verification = dict(result)
        activity.stop(f"verified in {self.verify_ms:.0f} ms")
        self._paint()

    def action_save(self) -> None:
        """Write the recipe into the workspace, at the path the hint showed."""
        if not self.recipe:
            self.app.notify("nothing to save yet — cook the pair first", timeout=4)
            return
        target = self._save_target()
        if target.exists() and not self._overwrite_armed:
            # One more press to overwrite: never silently replace a recipe.
            self._overwrite_armed = True
            self._notice = Text(
                f"  {target.name} already exists — press [S] again to overwrite it.",
                style="#ffb454",
            )
            self._paint_body()
            self._paint_hints()
            return
        import json

        try:
            target.write_text(json.dumps(self.recipe, indent=2), encoding="utf-8")
        except OSError as exc:
            self.app.notify(f"could not write {target.name}: {exc}", timeout=5)
            return
        self.saved_to = target
        self.app.note_recent(target, "recipe")
        self._overwrite_armed = False
        self._notice = None
        self._paint()
        self.app.notify(f"recipe written: {target.name}", timeout=4)

    def action_back(self) -> None:
        self.app.pop_screen()
