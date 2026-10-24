"""
Console plumbing that has to happen before anything is printed.

The one problem this solves: on Windows the console's encoding is the ANSI code
page (cp1252, cp437…), and Python uses it for a redirected stream as well — so

    openremap identify dump.bin > out.txt

writes with cp1252 and raises ``UnicodeEncodeError`` on the first em dash, arrow
or check mark. The product's own copy is full of them, on purpose: `→`, `✓`, `✗`,
`⚠`, `Δ`, `·`. Failing to *print* is not a reason to fail.

``errors="replace"`` rather than the default ``strict``: a terminal that cannot
represent a glyph should show something (``?``) and keep going, never take the
command down with it. On a UTF-8 console — every Linux and macOS terminal, and
Windows Terminal — this function changes nothing.
"""

from __future__ import annotations

import sys

__all__ = ["force_utf8"]

#: Streams whose encoding we control. ``sys.stdout``/``stderr`` only: they are
#: the ones a CLI, a TUI and the JSON-RPC transport write to.
_UTF8_NAMES = frozenset({"utf8", "utf-8", "utf_8"})


def force_utf8(*, errors: str = "replace") -> None:
    """Make ``stdout``/``stderr`` UTF-8, tolerating streams that cannot change.

    Silent by design: this runs on every start, and a host that has replaced
    ``sys.stdout`` with its own object (a test harness, an embedding app, a
    logging hijack) must not be told about it. The worst case of skipping is the
    behaviour we already had.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # not a TextIOWrapper: nothing to configure
            continue
        if str(getattr(stream, "encoding", "") or "").lower() in _UTF8_NAMES:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError, TypeError):
            # A closed or exotic stream: keep going, printing still works as well
            # as it did before this call.
            continue
