"""
The Home screen — the room you land in after boot.

It answers one question: *what do I want to do with an ECU file?* Six tasks,
each backed by a real command, plus the files you touched last and the state of
the engine. Nothing on it is decorative: the recent list is a real file on disk,
the status line reports the real backend, and a task that does not exist yet says
so instead of pretending.

Deliberate choices
------------------
* **Read-only by default.** BATCH opens the scan flow, which classifies and
  reports; moving files is a second, explicit step ("organize"), never the
  default — the same rule the CLI follows with ``--dry-run``.
* **No invented numbers.** If the recent list is empty, the screen says so.
* **The empty state gives a way in**: ``S`` builds a synthetic ECU, so someone
  with no dumps still sees the engine work, on data that is not a real ROM.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from openremap import __version__
from openremap.tui import paths, state
from openremap.tui.widgets.card import TaskCard

__all__ = ["TASKS", "HomeScreen"]

#: key, number, title, what it needs, what it produces
TASKS: tuple[tuple[str, int, str, str, str], ...] = (
    ("identify", 1, "IDENTIFY", "one dump", "family · SW · conf"),
    ("cook", 2, "COOK", "stock + tuned", "5.0 recipe"),
    ("diff", 3, "DIFF", "stock + tuned", "what changed"),
    ("apply", 4, "APPLY", "recipe + target", "tuned copy"),
    ("inspect", 5, "INSPECT", "one dump", "health · sums"),
    ("batch", 6, "BATCH", "a folder", "classify only"),
    ("maps", 7, "MAPS", "one or two dumps", "diff · CSV"),
    ("contribute", 8, "CONTRIBUTE", "your own dumps", "a new family"),
)


class HomeScreen(Screen):
    """Where the six tasks live."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "task('identify')", "Identify", show=False),
        Binding("2", "task('cook')", "Cook", show=False),
        Binding("3", "task('diff')", "Diff", show=False),
        Binding("4", "task('apply')", "Apply", show=False),
        Binding("5", "task('inspect')", "Inspect", show=False),
        Binding("6", "task('batch')", "Batch", show=False),
        Binding("7", "task('maps')", "Maps", show=False),
        Binding("8", "task('contribute')", "Contribute", show=False),
        Binding("s", "demo_ecu", "Demo ECU", show=False),
        # `?` is Shift+/ on most layouts, so it is an alias, not the way in.
        Binding("h", "help", "Help", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("f", "open_file", "Open file", show=False),
        Binding("r", "focus_recent", "Recent", show=False),
        # A plain letter, like every other key on this screen. Clearing the list
        # touches no file, so it does not need a shift, a modifier or a second
        # press. (`delete` is kept as an alias for people whose hands go there.)
        Binding("d", "clear_recent", "Clear recent", show=False),
        Binding("delete", "clear_recent", "Clear recent", show=False),
        Binding("escape", "focus_cards", "", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.state = state.load()

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="home-panel"):
            yield Static("What do you want to do?", id="home-title")
            with Horizontal(classes="card-row"):
                for spec in TASKS[:4]:
                    yield self._card(spec)
            with Horizontal(classes="card-row"):
                for spec in TASKS[4:]:
                    yield self._card(spec)
            yield Static("Recent", id="home-recent-title")
            yield OptionList(id="home-recent")
            yield Static("", id="home-recent-empty")
            with Horizontal(id="home-status"):
                yield Static("", id="home-status-left")
                yield Static("", id="home-status-hints")

    #: Tasks whose screen exists. Everything else is drawn as "soon" rather than
    #: announced as available — a card that does nothing is a broken promise.
    AVAILABLE = frozenset(
        {"identify", "cook", "diff", "apply", "inspect", "batch", "maps", "contribute"}
    )

    @classmethod
    def _card(cls, spec: tuple[str, int, str, str, str]) -> TaskCard:
        key, number, title, needs, produces = spec
        return TaskCard(key, number, title, needs, produces, enabled=key in cls.AVAILABLE)

    def on_mount(self) -> None:
        self._paint_recent()
        self._paint_status()
        self._paint_hints()
        first = self.query(TaskCard).first()
        if first is not None:
            first.focus()

    # -- painting -------------------------------------------------------
    def _paint_recent(self) -> None:
        """The files this workspace remembers, as something you can open."""
        listing = self.query_one("#home-recent", OptionList)
        listing.clear_options()
        entries = self.state.recent[:5]
        for entry in entries:
            name = entry.label or entry.path.rsplit("/", 1)[-1]
            when = entry.when()
            prompt = Text(f"  {name}")
            prompt.append(f"   {entry.kind}", style="#3a4557")
            if when:
                prompt.append(f"   {when}", style="#5a6b85")
            listing.add_option(Option(prompt, id=entry.path))

        empty = self.query_one("#home-recent-empty", Static)
        listing.display = bool(entries)
        if entries:
            empty.update("")
        else:
            text = Text()
            text.append("  nothing yet — identify a dump, cook a recipe, or press ", style="#4b5a70")
            text.append("S", style="bold #4ad6a8")
            text.append(" for a demo ECU", style="#4b5a70")
            empty.update(text)

    #: Keys that work when the recent list has focus, and when the cards do.
    _HINTS_CARDS = "1-7 open   R recent   F open   S demo   H help   Q quit"
    _HINTS_RECENT = "↑↓ choose   Enter open   D clear list   Esc back"

    def _paint_hints(self) -> None:
        """The bottom bar follows the focus — a key you cannot see is a key you
        do not have."""
        widget = self.query_one("#home-status-hints", Static)
        if isinstance(self.app.focused, OptionList):
            widget.update(Text(self._HINTS_RECENT, style="#4ad6a8"))
        else:
            widget.update(Text(self._HINTS_CARDS, style="#5a6b85"))

    def on_descendant_focus(self, event) -> None:
        self._paint_hints()

    def on_descendant_blur(self, event) -> None:
        self._paint_hints()

    def _paint_status(self) -> None:
        line = Text()
        line.append(f"v{__version__}", style="#3a4557")
        line.append("   ·   ", style="#1e2a38")
        line.append(paths.label(paths.workspace()), style="#5a6b85")
        line.append("   ·   ", style="#1e2a38")
        ok, text = self._rust_status()
        line.append(text, style="#4ad6a8" if ok else "#ff6b6b")
        if not self.state.writable:
            line.append("   ·   recent not saved", style="#ffb454")
        self.query_one("#home-status-left", Static).update(line)

    @staticmethod
    def _rust_status() -> tuple[bool, str]:
        try:
            from openremap import _rust  # noqa: F401
        except ImportError:  # pragma: no cover - the boot screen would have failed
            return False, "engine missing"
        return True, "engine ok"

    def _note(self, message: str) -> None:
        self.app.notify(message, timeout=4)

    # -- actions --------------------------------------------------------
    def action_task(self, key: str) -> None:
        """Activate a task. Screens arrive one at a time; say so until then."""
        if key == "identify":
            from openremap.tui.screens.identify import IdentifyScreen

            self.app.push_screen(IdentifyScreen())
            return
        if key == "cook":
            from openremap.tui.screens.cook import CookScreen

            self.app.push_screen(CookScreen())
            return
        if key == "diff":
            from openremap.tui.screens.diff import DiffScreen

            self.app.push_screen(DiffScreen())
            return
        if key == "apply":
            from openremap.tui.screens.apply import ApplyScreen

            self.app.push_screen(ApplyScreen())
            return
        if key == "contribute":
            from openremap.tui.screens.contribute import ContributeScreen

            self.app.push_screen(ContributeScreen())
            return
        if key == "batch":
            from openremap.tui.screens.batch import BatchScreen

            self.app.push_screen(BatchScreen())
            return
        if key == "inspect":
            from openremap.tui.screens.inspect import InspectScreen

            self.app.push_screen(InspectScreen())
            return
        if key == "maps":
            from openremap.tui.screens.maps import MapsScreen

            self.app.push_screen(MapsScreen())
            return
        title = next(t for k, _, t, _, _ in TASKS if k == key)
        self._note(f"{title} — this screen is not built yet")

    # -- recent list ----------------------------------------------------
    def on_screen_resume(self) -> None:
        """Refresh on the way back — a screen you just used has recorded a file."""
        self.state = state.load()
        self._paint_recent()
        self._paint_status()
        self._paint_hints()

    def action_focus_recent(self) -> None:
        if not self.state.recent:
            self._note("nothing in the recent list yet")
            return
        listing = self.query_one("#home-recent", OptionList)
        listing.focus()
        # A freshly focused list highlights nothing, and Enter on nothing does
        # nothing: select the first row so the keys work immediately.
        if listing.option_count and listing.highlighted is None:
            listing.highlighted = 0

    def action_focus_cards(self) -> None:
        first = self.query(TaskCard).first()
        if first is not None:
            first.focus()

    def action_clear_recent(self) -> None:
        """Forget the list. The files are untouched, so this is one keystroke."""
        if not self.state.recent:
            self._note("the recent list is already empty")
            return
        count = len(self.state.recent)
        self.state = state.clear_recent()
        self._paint_recent()
        self._paint_hints()
        self._note(
            f"forgot {count} recent {'file' if count == 1 else 'files'} — "
            "the files themselves are untouched"
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Open a remembered file with the screen that suits its kind."""
        path = str(event.option.id or "")
        entry = next((r for r in self.state.recent if r.path == path), None)
        if entry is None:
            return
        kind = entry.kind
        if kind == "recipe":
            from openremap.tui.screens.apply import ApplyScreen

            self.app.push_screen(ApplyScreen(recipe=path))
            return
        if kind == "folder":
            from openremap.tui.screens.batch import BatchScreen

            self.app.push_screen(BatchScreen(folder=path))
            return
        # "dump" and "patched" are binaries: identify is the first honest look.
        from openremap.tui.screens.identify import IdentifyScreen

        self.app.push_screen(IdentifyScreen(path))

    def action_help(self) -> None:
        """What the tool can do, generated from the engine's own registry."""
        from openremap.tui.screens.help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_open_file(self) -> None:
        """Pick a dump and go straight to IDENTIFY with it loaded."""
        from openremap.tui.screens.identify import IdentifyScreen
        from openremap.tui.screens.picker import FilePicker

        def _chosen(path) -> None:
            if path is not None:
                self.app.push_screen(IdentifyScreen(str(path)))

        self.app.push_screen(FilePicker(), _chosen)

    def on_task_card_activated(self, event: TaskCard.Activated) -> None:
        self.action_task(event.key)

    def action_demo_ecu(self) -> None:
        """Generate a synthetic ECU into the workspace — no real ROM involved."""
        import base64

        from openremap.api import call

        target = paths.subdir("ecus") / "demo_ecu.bin"
        replaced = target.exists()
        try:
            result = call("generate_dump", {})
            target.write_bytes(base64.b64decode(result["data_b64"]))
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            self._note(f"demo ECU failed: {type(exc).__name__}: {exc}")
            return
        self.state = state.record_recent(target, "bin", target.name)
        self._paint_recent()
        self._paint_status()
        self._note(
            f"demo ECU {'replaced' if replaced else 'written'} at {paths.label(target)}"
        )
