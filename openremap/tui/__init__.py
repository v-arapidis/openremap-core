"""
openremap.tui — the terminal UI.

A from-scratch Textual application. It is a **consumer**: every piece of work
goes through the API gate (``openremap.api``), never through the services
directly — the rule the previous implementation broke.

Layout
------
``main.py``        entry point for the ``openremap-tui`` console script
``app.py``         the application shell
``screens/``       one screen per moment in the flow (boot first, then the work)
``theme.tcss``     the stylesheet

Status: BOOT, HOME, IDENTIFY, INSPECT, MAPS and COOK exist; APPLY, BATCH and
CONTRIBUTE are still being built, and HOME marks them "· soon" rather than
offering a dead key.
"""

from __future__ import annotations

__all__ = ["run"]


def run() -> None:
    """Launch the TUI."""
    from openremap.tui.main import run as _run

    _run()
