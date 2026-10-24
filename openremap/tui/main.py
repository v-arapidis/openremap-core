"""
OpenRemap TUI — entry point.

Called by the ``openremap-tui`` console script declared in ``pyproject.toml``.
"""

from __future__ import annotations

import sys


def run(argv: list[str] | None = None) -> None:
    """Launch the OpenRemap Terminal User Interface."""
    from openremap.tui.app import OpenRemapTUI

    args = sys.argv[1:] if argv is None else argv
    app = OpenRemapTUI()
    # ``--fast`` skips the decorative pause on the boot screen. The boot work
    # itself always runs — only the flourish is optional.
    if "--fast" in args:
        app.fast_boot = True
    app.run()


if __name__ == "__main__":
    run()
