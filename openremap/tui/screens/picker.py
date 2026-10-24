"""
Picking a file, in-app.

Two things this screen must get right, because the previous TUI got both wrong:

* **No desktop dialogs.** The old shell ran ``zenity`` / ``kdialog``: needs an X
  session, flashes a window over the terminal, unusable from the keyboard.
* **You can go where the dumps actually are.** A picker that only shows the
  workspace is useless on a fresh install (the workspace has no binaries) — so
  this one starts where you last chose something, else the workspace ``ecus/``
  folder, else ``Downloads``, else home; shows the current directory as a
  breadcrumb; and goes *up* a level with ``←`` / ``Backspace``.

Arrow keys drive the tree, typing goes to the path field, ``Enter`` opens or
chooses, ``Esc`` cancels.
"""

from __future__ import annotations

import os
import string
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Input, Static

__all__ = ["BINARY_SUFFIXES", "RECIPE_SUFFIXES", "FilePicker"]

#: Suffixes the engine can read (raw dumps and the two text containers).
BINARY_SUFFIXES = (".bin", ".ori", ".hex", ".s19", ".srec", ".mot")

#: Suffixes of a recipe — a JSON document, not a binary.
RECIPE_SUFFIXES = (".remap",)


def _has_content(folder: Path, suffixes: tuple[str, ...] = BINARY_SUFFIXES) -> bool:
    """True when *folder* would show something in the tree."""
    try:
        for child in folder.iterdir():
            if child.is_dir() or child.suffix.lower() in suffixes:
                return True
    except OSError:
        return False
    return False


def _typed_path(raw: str, *, windows: bool | None = None) -> Path:
    """A path the user typed in the field, with Windows' drive trap removed.

    On Windows ``D:`` means "the current directory on D:" — a *drive-relative*
    path, not the drive root — so somebody typing ``D:`` to switch drives lands
    somewhere they did not mean, or nowhere at all. It is read as ``D:\\`` here.
    On POSIX the same text is just a filename that starts with a letter, so the
    rule is applied only where it means something.
    """
    return Path(normalize_typed(raw, windows=windows)).expanduser()


def normalize_typed(raw: str, *, windows: bool | None = None) -> str:
    """The typed text, with Windows' drive trap removed — **pure string work**.

    Split out from :func:`_typed_path` on purpose. Anything that goes through
    ``Path`` cannot be asserted off Windows: ``str(PureWindowsPath(r"\\\\server\\share"))``
    is ``'\\\\server\\share\\'`` because pathlib appends the root separator to a UNC
    drive, while on POSIX the identical text is just a filename. A test that
    compares the resulting *path string* therefore tests the runner's flavour, not
    the rule — twice in one file, before this split.

    The rule itself: only a *bare* drive spec (``D:``, ``d:``) is rewritten.
    ``D:`` is drive-relative on Windows ("the current directory on D:"), which is
    not what anyone typing it means.
    """
    windows = (os.name == "nt") if windows is None else windows
    text = raw.strip()
    if windows and len(text) == 2 and text[1] == ":" and text[0].isalpha():
        return f"{text[0]}:\\"
    return text


def available_drives(
    *, windows: bool | None = None, exists: Callable[[str], bool] | None = None
) -> list[str]:
    """The drive roots on Windows, ``[]`` everywhere else.

    A tree rooted at ``C:\\Users\\...`` can only climb as far as ``C:\\`` — there
    are no arrow keys that leave a drive — so on Windows the drives have to be
    *discoverable* somewhere. They are listed in the picker's header and typed
    into the path field (``D:`` is normalised to ``D:\\`` by ``_typed_path``);
    no jump key is invented, because a printable key here belongs to the field.

    *exists* is injectable so the enumeration can be tested off Windows.
    """
    windows = (os.name == "nt") if windows is None else windows
    if not windows:
        return []
    probe = exists or (lambda path: Path(path).is_dir())
    return [
        f"{letter}:\\"
        for letter in string.ascii_uppercase
        if probe(f"{letter}:\\")
    ]


def default_start() -> Path:
    """Where to open: last place used, else somewhere with dumps in it."""
    from openremap.tui import paths, state

    remembered = state.load().last_dirs.get("open")
    candidates = [
        Path(remembered) if remembered else None,
        paths.workspace() / "ecus",
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home(),
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and _has_content(candidate):
            return candidate
    return Path.home()


class _BinaryTree(DirectoryTree):
    """A directory tree that only shows directories and files it can accept."""

    #: Set by the picker before mounting: binaries, or recipes.
    suffixes: tuple[str, ...] = BINARY_SUFFIXES

    def filter_paths(self, paths) -> list[Path]:
        return [
            path
            for path in paths
            if path.is_dir() or path.suffix.lower() in self.suffixes
        ]


class FilePicker(ModalScreen[Path | None]):
    """Choose a binary. Returns the path, or ``None`` on cancel."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("left", "up", "Up a level", show=True),
        Binding("backspace", "up", "Up a level", show=False),
        # Only advertised (and only meaningful) when the caller wants a folder.
        Binding("space", "choose_folder", "This folder", show=False),
    ]

    def __init__(
        self,
        start: Path | None = None,
        *,
        title: str = "Open a dump",
        suffixes: tuple[str, ...] = BINARY_SUFFIXES,
        folders: bool = False,
    ) -> None:
        super().__init__()
        self.start = (start if start and start.is_dir() else default_start()).resolve()
        self.title_text = title
        self.suffixes = suffixes
        #: When true the picker may also return the folder it is showing —
        #: a batch scan needs a folder, not a file.
        self.folders = folders

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="picker-panel"):
            yield Static(self.title_text, id="picker-title")
            yield Static("", id="picker-breadcrumb")
            yield Input(placeholder="type a path and press Enter", id="picker-path")
            yield Static("", id="picker-drives")
            tree = _BinaryTree(str(self.start), id="picker-tree")
            tree.suffixes = self.suffixes
            yield tree
            hint = "↑↓ move   Enter open   ← up   type a path   Esc cancel"
            if self.folders:
                hint += "   Space this folder"
            hint += f"      ·  accepts {' '.join(self.suffixes)}"
            yield Static(hint, id="picker-hint")

    def on_mount(self) -> None:
        # The tree takes focus so the arrow keys work straight away; typing is
        # redirected to the path field by ``on_key``.
        self.query_one("#picker-tree", _BinaryTree).focus()
        self._paint_drives()
        self._paint_breadcrumb()

    def _paint_drives(self) -> None:
        """Say which drives exist, and how to get to one.

        Hidden entirely off Windows: a list of drive letters on Linux would be a
        line of noise about a concept the platform does not have.
        """
        drives = available_drives()
        row = self.query_one("#picker-drives", Static)
        if not drives:
            row.display = False
            return
        text = Text("  drives  ")
        for drive in drives:
            text.append(f"{drive}  ", style="#4ad6a8")
        text.append(" type one to go there, e.g. ", style="#5a6b85")
        text.append(f"{drives[-1]}", style="#8aa0c0")
        text.append(" then Enter", style="#5a6b85")
        row.update(text)

    def _paint_breadcrumb(self) -> None:
        """Say where we are, short: ``~/Documents/OpenRemap/ecus``, not the full path.

        The parent is one ``←`` away and the hint bar already says so; printing
        it here only pushed the useful part off the edge of the panel.
        """
        from openremap.tui import paths

        current = self.query_one("#picker-tree", _BinaryTree).path
        crumb = Text()
        crumb.append("  ", style="#3a4557")
        crumb.append(paths.label(current), style="#5a6b85")
        self.query_one("#picker-breadcrumb", Static).update(crumb)

    # -- choosing -------------------------------------------------------
    def accepts(self, path: Path) -> bool:
        """One list decides what this picker shows *and* what it takes back."""
        return path.is_file() and path.suffix.lower() in self.suffixes

    def _choose(self, path: Path) -> None:
        if self.accepts(path):
            self._remember(path.parent)
            self.dismiss(path.resolve())
            return
        # Never fail silently: name the file and what this picker wants.
        wanted = " ".join(self.suffixes)
        self.notify(f"{path.name} is not one of: {wanted}", timeout=4)

    @staticmethod
    def _remember(folder: Path) -> None:
        from openremap.tui import state

        current = state.load()
        current.last_dirs["open"] = str(folder)
        state.save(current)

    def action_up(self) -> None:
        tree = self.query_one("#picker-tree", _BinaryTree)
        parent = tree.path.parent
        if parent != tree.path:
            tree.path = parent
            self.query_one("#picker-path", Input).value = ""
            self._paint_breadcrumb()

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self._paint_breadcrumb()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._choose(Path(event.path))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            return
        path = _typed_path(raw)
        if path.is_file():
            self._choose(path)
        elif path.is_dir():
            self.query_one("#picker-tree", _BinaryTree).path = path.resolve()
            self._paint_breadcrumb()
            self.query_one("#picker-path", Input).value = ""
        else:
            # The same rule `_choose` follows: never fail silently. A typo used to
            # leave the tree exactly where it was, with no message at all.
            self.notify(f"no such folder: {raw}", timeout=4)

    def chosen_folder(self) -> Path:
        """The folder the user is pointing at.

        The highlighted entry decides: a directory means that directory, a file
        means the directory holding it, and the tree's own root means itself.
        Using the tree's ``path`` alone would always answer "the folder the tree
        opened", which is not what the highlight says.
        """
        tree = self.query_one("#picker-tree", _BinaryTree)
        node = tree.cursor_node
        entry = getattr(node, "data", None)
        path = getattr(entry, "path", None)
        if path is None:
            return tree.path
        candidate = Path(path)
        return candidate if candidate.is_dir() else candidate.parent

    def action_choose_folder(self) -> None:
        """Return the highlighted folder (only when the caller asked for one)."""
        if not self.folders:
            self.notify("this picker wants a file — press Enter on one", timeout=3)
            return
        target = self.chosen_folder()
        self._remember(target)
        self.dismiss(target.resolve())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        field = self.query_one("#picker-path", Input)
        # A printable key never reaches a binding here — it becomes text in the
        # path field. So the one printable key that means something in a folder
        # picker is handled before that redirect. (A binding on the screen or on
        # the tree is never consulted for it: proven by watching the field.)
        if event.key == "space" and self.folders and not field.has_focus:
            self.action_choose_folder()
            event.stop()
            return
        if event.key == "down" and field.has_focus:
            self.query_one("#picker-tree", _BinaryTree).focus()
            event.stop()
            return
        if event.is_printable and not field.has_focus:
            field.focus()
            field.insert_text_at_cursor(event.character)
            event.stop()
