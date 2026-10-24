"""
Tiny persistent state for the TUI — the recent-files list.

Deliberately boring: one JSON file, one version, a hard cap, and pruning of
entries whose file has since disappeared (a "recent" list that offers dead paths
is worse than an empty one).

Every read and write is best-effort. A read-only home yields an empty state and
a no-op write, never an exception — the same rule the boot screen follows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openremap.tui import paths

__all__ = ["Recent", "State", "clear_recent", "load", "record_recent"]

STATE_VERSION = 1
MAX_RECENT = 10


@dataclass
class Recent:
    path: str
    kind: str  # "bin" | "recipe"
    label: str = ""
    at: str = ""

    def exists(self) -> bool:
        # A file *or* a folder: the list remembers whatever the operator used,
        # and pruning must only drop what is actually gone.
        return Path(self.path).exists()

    def when(self) -> str:
        """Relative age, in words. Empty when the timestamp is unreadable."""
        if not self.at:
            return ""
        try:
            moment = datetime.strptime(self.at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return ""
        seconds = (datetime.now(timezone.utc) - moment).total_seconds()
        if seconds < 90:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86_400:
            return f"{int(seconds // 3600)}h ago"
        return f"{int(seconds // 86_400)}d ago"


@dataclass
class State:
    recent: list[Recent] = field(default_factory=list)
    last_dirs: dict[str, str] = field(default_factory=dict)
    #: False when the file could not be written — the UI says so instead of
    #: silently forgetting everything on the next launch.
    writable: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> State:
    """Read the state file, dropping entries whose file is gone."""
    target = paths.state_file()
    state = State()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state

    for item in raw.get("recent") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        recent = Recent(
            path=str(item["path"]),
            kind=str(item.get("kind") or "bin"),
            label=str(item.get("label") or ""),
            at=str(item.get("at") or ""),
        )
        if recent.exists():
            state.recent.append(recent)
    state.last_dirs = {
        str(k): str(v) for k, v in (raw.get("last_dirs") or {}).items() if isinstance(v, str)
    }
    return state


def clear_recent() -> State:
    """Forget the recent list (the files themselves are untouched)."""
    state = load()
    state.recent = []
    save(state)
    return state


def save(state: State) -> bool:
    """Write the state file; return False when the workspace is not writable."""
    payload = {
        "version": STATE_VERSION,
        "recent": [
            {"path": r.path, "kind": r.kind, "label": r.label, "at": r.at}
            for r in state.recent[:MAX_RECENT]
        ],
        "last_dirs": state.last_dirs,
    }
    try:
        paths.state_file().write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        state.writable = False
        return False
    return True


def record_recent(path: Path, kind: str, label: str = "") -> State:
    """Put *path* at the top of the recent list (no duplicates) and save."""
    state = load()
    entry = Recent(path=str(path), kind=kind, label=label, at=_now())
    state.recent = [entry] + [
        r for r in state.recent if Path(r.path) != Path(entry.path)
    ]
    del state.recent[MAX_RECENT:]
    save(state)
    return state
