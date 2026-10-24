"""
The OpenRemap TUI shell.

Right now the shell is deliberately thin: it owns the stylesheet, the theme and
the screen stack, and its only screen is the boot sequence. Feature screens are
added one at a time, each on top of the API gate.
"""

from __future__ import annotations

import pathlib
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding

from openremap import __version__
from openremap.tui import state as tui_state
from openremap.tui.screens.boot import BootScreen

__all__ = ["OpenRemapTUI"]


class OpenRemapTUI(App):
    """The application. Dark command-prompt, one accent, no chrome noise."""

    CSS_PATH = str(pathlib.Path(__file__).parent / "theme.tcss")
    TITLE = "OpenRemap"
    SUB_TITLE = f"v{__version__}"

    BINDINGS: ClassVar[list[Binding]] = [
        # `q` is NOT priority: any widget that wants the character as text (the
        # file picker's path field, above all) must get it first — otherwise a
        # path ending in "…q" would quit the app mid-sentence.
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    #: Set by ``main.run()`` when ``--fast`` is passed on the command line.
    fast_boot: bool = False

    #: Kinds a recent entry can have — the HOME screen routes on this.
    RECENT_KINDS = ("dump", "recipe", "patched", "folder")

    def note_recent(self, path: Path | str | None, kind: str) -> None:
        """Remember a file the operator actually used.

        One helper, called from every screen, so the list cannot mean different
        things in different places. It writes ``.state.json`` immediately: the
        point of the list is that it survives a restart.
        """
        if not path or kind not in self.RECENT_KINDS:
            return
        target = Path(path)
        if not target.exists():
            return
        try:
            tui_state.record_recent(target, kind, target.name)
        except OSError:
            # A read-only workspace loses the memory, not the session; the HOME
            # status line already says "recent not saved".
            self.log.warning("could not record %s in the recent list", target)

    def compose(self) -> ComposeResult:
        # Screens are pushed from ``on_mount`` so the app is already running when
        # the boot sequence starts its workers.
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(BootScreen(fast=self.fast_boot))
