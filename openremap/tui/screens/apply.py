"""
The APPLY screen — put a recipe onto a copy of the same ECU binary.

Three things have to be true before a byte is written, and the screen shows all
three: the **recipe** is a 5.0 document that says what it needs, the **target**
is a file of the declared size, and the **rung** being used is one the recipe
allows.

The rung is not a preference. ``tune-strict`` runs first — same firmware, every
change exactly where the recipe says, and nothing is guessed. When it refuses,
the refusal's own rule decides what may be offered next, and each offer is
printed with what it costs:

===============  =========================  ==========================================
rule             key                        what it gives up
===============  =========================  ==========================================
Γ2 identity      ``U`` tune-unique           the identity proof — offsets stay exact
Γ7 displaced     ``S`` tune-shift            the positional guarantee — ±2048, and an
                                            ambiguous anchor now refuses
Γ8 ambiguity     ``A`` tune-any              both, and ambiguity becomes a report
===============  =========================  ==========================================

Anything else (wrong size, wrong recipe, already patched, a failed check) has no
looser rung to offer, and the screen says so instead of suggesting a key that
would not help.

The result is a *new file*: the input is never written over, and the screen says
where the output goes before the key is pressed.
"""

from __future__ import annotations

import base64
import re
import textwrap
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
from openremap.tui.screens.picker import BINARY_SUFFIXES, RECIPE_SUFFIXES, FilePicker
from openremap.tui.widgets.activity import ActivityBar

__all__ = ["ApplyScreen"]

#: checksum verdict → (phrase, colour). The three answers the disclosure can give,
#: and none of them is "safe to flash": this engine writes calibrated bytes only.
_CHECKSUM_STATUS: dict[str, tuple[str, str]] = {
    "ok": ("every scheme we know still verifies", "#4ad6a8"),
    "fail": ("STALE — the patch broke a checksum we can verify", "#ff6b6b"),
    "skip": ("no known scheme for this family — nothing was checked", "#ffb454"),
}

#: (rung that failed, rule that stopped it) → (key, rung to use, what it costs).
#: The *reason* picks the relaxation: identity and position are independent
#: guarantees, so a failure in one is not an argument for giving up the other.
_ESCALATION: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("strict", "Γ2"): (
        "u",
        "unique",
        "apply without the identity proof",
        "positions stay exact, and an ambiguous anchor becomes a refusal",
    ),
    ("strict", "Γ7"): (
        "s",
        "shift",
        "allow the anchor to be found within ±2048 bytes",
        "same firmware required, and an ambiguous anchor now refuses",
    ),
    ("shift", "Γ2"): (
        "a",
        "any",
        "apply without the identity proof either",
        "the loosest rung: ±2048 and ambiguity reported instead of refused",
    ),
    ("shift", "Γ8"): (
        "a",
        "any",
        "accept ambiguous anchors as a report",
        "the loosest rung: no identity, ±2048, ambiguity reported",
    ),
    ("strict", "Γ4"): (
        "s",
        "shift",
        "let the anchor decide the position instead of the recorded offset",
        "same firmware required, ±2048, and an ambiguous anchor refuses",
    ),
    ("unique", "Γ4"): (
        "a",
        "any",
        "drop the original-bytes proof as well",
        "the loosest rung: no identity, ±2048, ambiguity reported",
    ),
    ("unique", "Γ7"): (
        "a",
        "any",
        "allow the anchor to be found within ±2048 bytes",
        "the loosest rung: no identity, ±2048, ambiguity reported",
    ),
}

#: Rules where nothing looser can help — the file or the recipe is the problem.
_DEAD_END = frozenset({"Γ1", "Γ5", "Γ6", "Δ1", "Δ4", "A1", "A2", "A3", "A4", "A5", "A12"})


def _escalation_for(rung: str, rule: str) -> tuple[str, str, str, str] | None:
    return _ESCALATION.get((rung, rule))


#: Rung name → the API method and a plain-language policy line.
_RUNG_COMMANDS = {
    "strict": "apply_strict",
    "shift": "apply_shift",
    "unique": "apply_unique",
    "any": "apply_any",
}


def _size_label(size: int | None) -> str:
    if not size:
        return "?"
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.0f} KB"


def _short(name: str, limit: int = 34) -> str:
    if len(name) <= limit:
        return name
    keep = limit - 1
    head = keep // 2
    return f"{name[:head]}…{name[-(keep - head):]}"


class ApplyScreen(Screen):
    """Apply a recipe to a target binary, on the safest rung that works."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "pick_recipe", "Recipe", show=True),
        Binding("t", "pick_target", "Target", show=True),
        Binding("enter", "apply", "Apply", show=True),
        # Contextual: only advertised when the refusal's rule allows them.
        Binding("u", "escalate('unique')", "", show=False),
        Binding("s", "escalate('shift')", "", show=False),
        Binding("a", "escalate('any')", "", show=False),
        Binding("d", "recipe_detail", "", show=False),
        Binding("w", "write", "", show=False),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, recipe: str | None = None, target: str | None = None) -> None:
        super().__init__()
        self.recipe_path = recipe
        self.target_path = target
        self.recipe: dict[str, Any] | None = None
        self.recipe_error: str | None = None
        self.target_facts: dict[str, Any] = {}
        self.rung = "strict"
        self.report: dict[str, Any] | None = None
        self.applied: bool = False
        self.output_sha256: str | None = None
        self.output_size: int = 0
        self._patched: bytes | None = None
        self.saved_to: Path | None = None
        self._overwrite_armed = False
        self.elapsed_ms = 0.0

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="apply-panel"):
            yield Static("", id="apply-title")
            yield Static("", id="apply-recipe")
            yield Static("", id="apply-target")
            yield Static("", id="apply-basis")
            yield ActivityBar(id="apply-activity")
            yield Static("", id="apply-body")
            yield Static("", id="apply-hints")

    def on_mount(self) -> None:
        self.query_one("#apply-title", Static).update(
            Text(
                "APPLY — put a recipe onto a copy of the same ECU binary",
                style="#5a6b85",
            )
        )
        if self.recipe_path:
            self._read_recipe()
        if self.target_path:
            self._read_target()
        self._paint()

    # -- reading the two inputs ------------------------------------------
    def _read_recipe(self) -> None:
        from openremap.api import call

        self.recipe = None
        self.recipe_error = None
        try:
            self.recipe = call("recipe_info", {"path": self.recipe_path})
        except Exception as exc:  # noqa: BLE001 - a bad recipe is a state, not a crash
            self.recipe_error = str(exc)

    def _read_target(self) -> None:
        from openremap.api import call

        self.target_facts = {}
        if not self.target_path:
            return
        path = Path(self.target_path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        identity: dict[str, Any] = {}
        try:
            identity = call("identify", {"path": str(path)})
        except Exception:  # noqa: BLE001 - unidentified is a fact, not an error
            identity = {}
        self.target_facts = {
            "size": size,
            "match_key": identity.get("match_key"),
            "family": identity.get("ecu_family"),
        }

    # -- painting ---------------------------------------------------------
    def _paint(self) -> None:
        self._paint_inputs()
        self._paint_basis()
        self._paint_body()
        self._paint_hints()

    def _paint_inputs(self) -> None:
        recipe_line = Text()
        recipe_line.append("  recipe  ", style="#3a4557")
        recipe_line.append("[R]", style="#4ad6a8")
        recipe_line.append("  ", style="#1e2a38")
        if not self.recipe_path:
            recipe_line.append("choose a .remap file", style="#5a6b85")
        elif self.recipe_error:
            recipe_line.append(_short(Path(self.recipe_path).name, 34), style="#ff6b6b")
            recipe_line.append("   not usable", style="#ff6b6b")
        else:
            info = self.recipe or {}
            recipe_line.append(_short(Path(self.recipe_path).name, 34), style="#c8d1e0")
            recipe_line.append(
                f"   {info.get('instructions')} instr · "
                f"floor {(info.get('contract') or {}).get('identity_floor')}",
                style="#8aa0c0",
            )
            key = (info.get("source") or {}).get("match_key")
            if key:
                recipe_line.append(f"   from {key}", style="#5a6b85")
        self.query_one("#apply-recipe", Static).update(recipe_line)

        target_line = Text()
        target_line.append("  target  ", style="#3a4557")
        target_line.append("[T]", style="#4ad6a8")
        target_line.append("  ", style="#1e2a38")
        if not self.target_path:
            target_line.append("choose the binary to patch", style="#5a6b85")
        else:
            target_line.append(_short(Path(self.target_path).name, 34), style="#c8d1e0")
            target_line.append(f"   {_size_label(self.target_facts.get('size'))}", style="#3a4557")
            key = self.target_facts.get("match_key")
            if key:
                target_line.append(f"   {key}", style="#8aa0c0")
            elif self.target_facts:
                target_line.append("   not recognised", style="#5a6b85")
        self.query_one("#apply-target", Static).update(target_line)

    def _paint_basis(self) -> None:
        """What is known before the engine runs, then the rung in use."""
        line = Text()
        info = self.recipe or {}
        source = info.get("source") or {}
        if self.recipe_error:
            line.append("  ✗ the recipe cannot be read", style="bold #ff6b6b")
            line.append(" — pick another file", style="#3a4557")
        elif self.recipe and self.target_facts:
            recipe_size, target_size = source.get("size"), self.target_facts.get("size")
            if recipe_size and target_size and recipe_size != target_size:
                line.append("  ✗ different sizes", style="#ffb454")
                line.append(
                    f" — recipe source {_size_label(recipe_size)} vs target "
                    f"{_size_label(target_size)}; offsets only mean something at the "
                    "same size",
                    style="#3a4557",
                )
            else:
                line.append("  ✓ same size", style="#4ad6a8")
                recipe_key = source.get("match_key")
                target_key = self.target_facts.get("match_key")
                if recipe_key and target_key:
                    if recipe_key == target_key:
                        line.append("   ·   ", style="#1e2a38")
                        line.append("✓ same firmware", style="#4ad6a8")
                        line.append(f"   {recipe_key}", style="#5a6b85")
                    else:
                        line.append("   ·   ", style="#1e2a38")
                        line.append("✗ different firmware", style="#ffb454")
                        line.append(" — strict will refuse this target", style="#3a4557")
        self.query_one("#apply-basis", Static).update(line)

    def _paint_body(self) -> None:
        body = self.query_one("#apply-body", Static)
        if self.report is None:
            body.update(self._idle_body())
            return
        text = Text()
        if self.report.get("refusal"):
            self._paint_refusal(text)
        elif self.applied:
            self._paint_result(text)
        body.update(text)

    def _idle_body(self) -> Text:
        text = Text()
        rung_policy = {
            "strict": (
                "STRICT — the firmware must match and every change must be exactly\n"
                "  where the recipe says. This is the rung that cannot be wrong."
            ),
            "shift": (
                "SHIFT — the firmware must match, and a change may be found up to\n"
                "  2048 bytes away from its recorded offset."
            ),
            "unique": (
                "UNIQUE — no identity check; instead every original byte must be at\n"
                "  its recorded offset and every anchor must be unique."
            ),
            "any": (
                "ANY — the loosest rung: no identity, a displaced anchor is accepted,\n"
                "  and an ambiguous one is reported rather than refused."
            ),
        }[self.rung]
        text.append(f"  {rung_policy}\n\n", style="#3a4557")
        if self.recipe and self.target_facts and not self.recipe_error:
            text.append("  → press ", style="#5a6b85")
            text.append("[Enter]", style="bold #4ad6a8")
            text.append(f" to apply ({self.rung})", style="#5a6b85")
            text.append("   ·   nothing is written until you say so", style="#3a4557")
        elif self.recipe_error:
            text.append("  → press ", style="#5a6b85")
            text.append("[R]", style="bold #4ad6a8")
            text.append(" to choose a usable recipe", style="#5a6b85")
        else:
            missing = "recipe" if not self.recipe_path else "target"
            key = "[R]" if missing == "recipe" else "[T]"
            text.append("  → press ", style="#5a6b85")
            text.append(key, style="bold #4ad6a8")
            text.append(f" to choose the {missing}", style="#5a6b85")
        return text

    def _paint_refusal(self, text: Text) -> None:
        refusal = (self.report or {}).get("refusal") or {}
        rule = str(refusal.get("check") or "?")
        text.append("  ✗ REFUSED", style="bold #ff6b6b")
        text.append(f" — {rule}\n\n", style="#ff8a8a")
        text.append(f"     {refusal.get('message')}\n", style="#c8d1e0")
        escalation = _escalation_for(self.rung, rule)
        if escalation:
            key, next_rung, what, cost = escalation
            text.append("\n     ", style="#3a4557")
            text.append(f"[{key.upper()}]", style="bold #ffb454")
            text.append(f" {what} ", style="#c8d1e0")
            text.append(f"({next_rung})\n", style="#8aa0c0")
            text.append(f"         gives up: {cost}\n", style="#5a6b85")
        elif rule in _DEAD_END:
            text.append(
                "\n     no looser rung helps here — the file or the recipe is the problem.\n",
                style="#5a6b85",
            )

    def _paint_result(self, text: Text) -> None:
        report = self.report or {}
        counts = report.get("instructions") or {}
        policy = report.get("policy") or {}

        text.append("  ✓ APPLIED", style="bold #4ad6a8")
        text.append(
            f"    {counts.get('applied')} instructions · "
            f"{int(counts.get('bytes_written') or 0):,} bytes\n\n",
            style="#8aa0c0",
        )
        text.append(
            f"     POLICY     identity {policy.get('identity')} · "
            f"positions {policy.get('positions')} · ambiguity {policy.get('ambiguity')}\n",
            style="#8aa0c0",
        )
        # The strongest claim the tool can make, and it is checkable: the patched
        # bytes equal the file the recipe was cooked from, hash for hash.
        expected = ((self.recipe or {}).get("tune") or {}).get("sha256")
        if expected and self.output_sha256 == expected:
            text.append("     RESULT     byte-identical to the file this recipe was cooked from ✓\n",
                        style="#4ad6a8")
        elif expected:
            text.append("     RESULT     differs from the file this recipe was cooked from ✗\n",
                        style="#ffb454")
        text.append(f"                sha256 {str(self.output_sha256 or '')[:24]}…", style="#5a6b85")
        text.append(f"   ({_size_label(self.output_size)})\n", style="#5a6b85")

        # The disclaimer that "APPLIED" alone does not carry: the recipe verified,
        # the *file* may still be unflashable because nothing here rewrites a
        # stored checksum. Measured on the patched bytes, so it says which of the
        # three honest answers applies instead of a generic warning.
        checksums = (report.get("result") or {}).get("checksums") or {}
        if checksums:
            phrase, colour = _CHECKSUM_STATUS.get(
                checksums.get("status"), ("unknown", "#ffb454")
            )
            text.append("     CHECKSUMS  ", style="#3a4557")
            text.append("not corrected", style="bold #ffb454")
            text.append("\n")
            text.append(f"                {phrase}\n", style=colour)
            if checksums.get("status") == "fail" and checksums.get("message"):
                text.append(f"                {checksums['message']}\n", style=colour)
            text.append(
                "                verify: openremap checksum <written file>\n",
                style="#5a6b85",
            )
        # The boundary of what this file may be used for. Set apart from the
        # checks in a colour nothing else on this screen uses, because it is the
        # one line that is about the *car*, not about the bytes.
        disclaimer = (report.get("result") or {}).get("disclaimer")
        if disclaimer:
            lines = _sentence_lines(disclaimer, width=80 - 16)
            text.append("     DISCLAIMER ", style="bold #5fd7e0")
            text.append(f"{lines[0]}\n", style="#5fd7e0")
            for line in lines[1:]:
                text.append(f"                {line}\n", style="#5fd7e0")

        anchors = report.get("anchors") or {}
        text.append(
            f"     ANCHORS    {anchors.get('total', 0)} total · "
            f"{anchors.get('shifted', 0)} displaced"
            + (f" (max {anchors.get('max_shift')} B)" if anchors.get("shifted") else "")
            + f" · {anchors.get('multi_hit', 0)} with more than one copy\n",
            style="#5a6b85",
        )
        text.append("     CHECKS\n", style="#8aa0c0")
        for check in report.get("checks") or []:
            verdict = str(check.get("verdict") or "")
            mark = {"pass": "✓", "refuse": "✗", "info": "·"}.get(verdict, "·")
            style = {"pass": "#4ad6a8", "refuse": "#ff6b6b", "info": "#5a6b85"}.get(
                verdict, "#5a6b85"
            )
            text.append(f"     {mark} {check.get('id')!s:<9}", style=style)
            text.append(f"{_short(str(check.get('name')), 22):<23}", style="#c8d1e0")
            text.append(f"{_short(str(check.get('detail')), 36)}\n", style="#5a6b85")

    def _target_output(self) -> Path:
        name = Path(self.target_path or "output.bin")
        return paths.subdir("tunes") / f"{name.stem}_tuned{name.suffix or '.bin'}"

    def _write_status(self) -> Text:
        line = Text()
        if not self.applied:
            return line
        label = paths.label(self._target_output())
        if self.saved_to and not self._overwrite_armed:
            prefix = "  ✓ SAVED  "
            line.append(prefix, style="bold #4ad6a8")
            line.append(_short(label, 78 - len(prefix)), style="#4ad6a8")
        elif self._overwrite_armed:
            prefix = "  ⚠ EXISTS — press [W] again to overwrite"
            line.append(prefix, style="bold #ffb454")
            line.append(f"  {_short(label, 78 - len(prefix) - 2)}", style="#ffb454")
        else:
            prefix = "  ⚠ NOT WRITTEN — press [W] to write"
            line.append(prefix, style="bold #ffb454")
            line.append(f" {_short(label, 78 - len(prefix) - 1)}", style="bold #ffb454")
        return line

    def _paint_hints(self) -> None:
        hints = Text("  ")
        hints.append("[R]", style="#4ad6a8")
        hints.append(" recipe", style="#3a4557")
        if self.recipe:
            hints.append("    ", style="#1e2a38")
            hints.append("[D]", style="#4ad6a8")
            hints.append(" what is inside it", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[T]", style="#4ad6a8")
        hints.append(" target", style="#3a4557")
        ready = bool(self.recipe and self.target_facts and not self.recipe_error)
        if ready and self.report is None:
            hints.append("    ", style="#1e2a38")
            hints.append("[Enter]", style="#4ad6a8")
            hints.append(f" apply ({self.rung})", style="#3a4557")
        rule = str(((self.report or {}).get("refusal") or {}).get("check") or "")
        escalation = _escalation_for(self.rung, rule) if rule else None
        if escalation:
            hints.append("    ", style="#1e2a38")
            hints.append(f"[{escalation[0].upper()}]", style="#ffb454")
            hints.append(f" {escalation[1]}", style="#3a4557")
        if self.applied and not self.saved_to:
            hints.append("    ", style="#1e2a38")
            hints.append("[W]", style="#4ad6a8")
            hints.append(" write" if not self._overwrite_armed else " overwrite", style="#3a4557")
        hints.append("    ", style="#1e2a38")
        hints.append("[Esc]", style="#4ad6a8")
        hints.append(" back", style="#3a4557")

        footer = Text()
        footer.append_text(self._write_status())
        footer.append("\n")
        footer.append_text(hints)
        self.query_one("#apply-hints", Static).update(footer)

    # -- actions ----------------------------------------------------------
    def action_recipe_detail(self) -> None:
        """Open the recipe's instruction list — "79 instructions" as rows."""
        if not self.recipe_path:
            self.app.notify("choose a recipe first", timeout=3)
            return
        from openremap.api import call
        from openremap.tui.screens.recipe import RecipeDetailScreen

        try:
            info = call(
                "recipe_info",
                {"path": self.recipe_path, "include_instructions": True},
            )
        except Exception as exc:  # noqa: BLE001 - a bad recipe is a state
            self.app.notify(f"cannot read the recipe: {type(exc).__name__}: {exc}", timeout=5)
            return
        self.app.push_screen(RecipeDetailScreen(info))

    def action_pick_recipe(self) -> None:
        def _chosen(path) -> None:
            if path is None:
                return
            self.recipe_path = str(path)
            self._reset_result()
            self._read_recipe()
            self._paint()

        start = Path(self.recipe_path).parent if self.recipe_path else None
        self.app.push_screen(
            FilePicker(start, title="Open a recipe", suffixes=RECIPE_SUFFIXES), _chosen
        )

    def action_pick_target(self) -> None:
        def _chosen(path) -> None:
            if path is None:
                return
            self.target_path = str(path)
            self._reset_result()
            self._read_target()
            self._paint()

        start = Path(self.target_path).parent if self.target_path else None
        self.app.push_screen(
            FilePicker(start, title="Open the target binary", suffixes=BINARY_SUFFIXES),
            _chosen,
        )

    def _reset_result(self) -> None:
        self.report = None
        self.applied = False
        self.output_sha256 = None
        self.output_size = 0
        self._patched = None
        self.saved_to = None
        self._overwrite_armed = False

    def action_apply(self) -> None:
        self._apply_rung("strict")

    def action_escalate(self, rung: str) -> None:
        """Take the looser rung the refusal asked for — and only that one."""
        rule = str(((self.report or {}).get("refusal") or {}).get("check") or "")
        allowed = _escalation_for(self.rung, rule)
        if allowed is None or allowed[1] != rung:
            self.app.notify("no looser rung helps with this refusal", timeout=3)
            return
        self._apply_rung(rung)

    def _apply_rung(self, rung: str) -> None:
        if not (self.recipe_path and self.target_path):
            self.app.notify("choose a recipe and a target first", timeout=3)
            return
        if not self.recipe:
            self.app.notify("this recipe cannot be read — pick another", timeout=3)
            return
        self.rung = rung
        self.app.note_recent(self.recipe_path, "recipe")
        self.app.note_recent(self.target_path, "dump")
        self._reset_result()
        self.run_worker(self._run(rung), name="apply", exclusive=True)

    async def _run(self, rung: str) -> None:
        import asyncio

        from openremap.api import call

        activity = self.query_one("#apply-activity", ActivityBar)
        activity.start(f"applying {rung} → {_size_label(self.target_facts.get('size'))}")
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                call,
                _RUNG_COMMANDS[rung],
                {
                    "path": self.target_path,
                    "recipe_path": self.recipe_path,
                },
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            activity.stop()
            self.query_one("#apply-body", Static).update(
                Text(f"  apply failed: {type(exc).__name__}: {exc}", style="bold #ff6b6b")
            )
            return

        self.elapsed_ms = (time.perf_counter() - started) * 1000
        self.report = dict(result.get("report") or {})
        self.applied = bool(result.get("applied"))
        self.output_sha256 = result.get("patched_sha256")
        payload = result.get("patched_b64")
        self._patched = base64.b64decode(payload) if payload else None
        self.output_size = len(self._patched or b"")
        activity.stop(f"{'applied' if self.applied else 'refused'} in {self.elapsed_ms:.0f} ms")
        self._paint()

    def action_write(self) -> None:
        """Write the patched bytes into the workspace, at the path shown."""
        if not self._patched:
            self.app.notify("nothing to write yet — apply the recipe first", timeout=4)
            return
        target = self._target_output()
        if target.exists() and not self._overwrite_armed:
            self._overwrite_armed = True
            self._paint_hints()
            return
        try:
            target.write_bytes(self._patched)
        except OSError as exc:
            self.app.notify(f"could not write {target.name}: {exc}", timeout=5)
            return
        self.saved_to = target
        self.app.note_recent(target, "patched")
        self._overwrite_armed = False
        self._paint_hints()
        self.app.notify(f"patched binary written: {target.name}", timeout=4)

    def action_back(self) -> None:
        self.app.pop_screen()


def _sentence_lines(text: str, width: int) -> list[str]:
    """Wrap *text* to *width*, one sentence per group.

    Sentence-wise rather than document-wise: a disclaimer broken mid-clause is
    the kind of text people skip, and this one is the point of the block.
    """
    lines: list[str] = []
    for sentence in re.split(r"(?<=\.)\s+", text):
        lines.extend(textwrap.wrap(sentence, width) or [sentence])
    return lines
