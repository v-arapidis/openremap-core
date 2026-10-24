"""
Where the TUI keeps things.

One place decides the workspace, because two screens asking the same question
two different ways is how a product ends up with files in three folders:

    Linux / macOS   ~/Documents/OpenRemap      (falls back to ~/OpenRemap when
                                                ~/Documents does not exist)
    Windows         %USERPROFILE%\\Documents\\OpenRemap

Inside it: ``ecus/`` (dumps), ``recipes/`` (``.remap`` files), ``tunes/``
(generated binaries), ``exports/`` (pictures and CSVs the DIFF screen
writes) and ``.state.json`` (recent files — see ``state.py``).

Nothing here raises on a read-only home: it reports what it cannot use and the
caller degrades. Analysis works without a writable workspace; only saving does
not.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["label", "state_file", "subdir", "workspace"]


def workspace() -> Path:
    """The OpenRemap workspace directory, created if possible."""
    documents = Path.home() / "Documents"
    base = documents if documents.is_dir() else Path.home()
    folder = base / "OpenRemap"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only home: the caller degrades, nobody crashes
    return folder


def subdir(name: str) -> Path:
    """``workspace()/name`` (``ecus`` / ``recipes`` / ``tunes``), created if possible."""
    folder = workspace() / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def state_file() -> Path:
    """The tiny JSON file that remembers recent files."""
    return workspace() / ".state.json"


def label(path: Path) -> str:
    """A short, human path: ``~/Documents/OpenRemap`` instead of the full one."""
    text = str(path)
    home = str(Path.home())
    if text.startswith(home):
        return "~" + os.sep.join(text[len(home) :].split(os.sep))
    return text
