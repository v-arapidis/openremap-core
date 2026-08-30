"""
openremap commands

Print a compact cheat-sheet of every available command — one line per command,
syntax + one-sentence description.  Designed for returning users who know the
workflow and just need a quick reminder.

Examples:
    openremap commands
"""

from __future__ import annotations

import typer

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_W = 73  # separator width


def _sep() -> None:
    typer.echo(typer.style("  " + "─" * _W, dim=True))


def _blank() -> None:
    typer.echo("")


def _describe(info) -> str:
    """One-line description for a registered command: the explicit help (or
    short help) text, else the callback docstring's first non-empty line
    (the `validate` sub-app sets no ``help=`` — its docstring carries it)."""
    txt = info.help or info.short_help or ""
    if not txt and info.callback and info.callback.__doc__:
        for line in info.callback.__doc__.splitlines():
            if line.strip():
                txt = line.strip()
                break
    return txt.strip().replace("\n", " ")


# ---------------------------------------------------------------------------
# Command table
# ---------------------------------------------------------------------------
# The cheat-sheet rows are DERIVED at runtime from the Typer app registry
# (openremap.core.cli.main.app) — never a hardcoded list.  Adding/renaming a
# command in main.py automatically updates this cheat-sheet.

# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def commands() -> None:
    """
    Print a one-line-per-command cheat-sheet of every openremap command.

    Use this as a quick reminder when you already know the workflow.
    Run  openremap workflow  for a full plain-English walkthrough.
    Run  openremap <command> --help  for complete options on any single command.
    """
    # Deferred import — cmds.py is imported by main.py at module load, so a
    # top-level import here would be circular; by the time this function runs
    # the app is fully built.
    from openremap.core.cli.main import app

    _blank()
    typer.echo(typer.style("  OpenRemap — Command Reference", bold=True))
    _sep()
    _body = (
        "  Run  openremap <command> --help  for full options on any command.\n"
        "  Run  openremap workflow           for the complete step-by-step guide."
    )
    typer.echo(_body)
    _sep()
    _blank()

    # Derive every row from the Typer registry — never a hardcoded list, so
    # adding a command to main.py updates this cheat-sheet automatically.
    rows: list[tuple[str, str]] = [
        ("openremap", "Launch the full terminal UI (no arguments needed)."),
    ]
    for info in app.registered_commands:
        rows.append((f"openremap {info.name}", _describe(info)))
    for group in app.registered_groups:
        gname = group.name or ""
        if isinstance(group.help, str):  # group line only when a help exists
            rows.append((f"openremap {gname}", group.help.strip().replace("\n", " ")))
        for sub in group.typer_instance.registered_commands:
            rows.append((f"openremap {gname} {sub.name}", _describe(sub)))

    # Calculate column width from the longest syntax string
    max_syn = max(len(syn) for syn, _ in rows)
    col = max_syn + 3  # padding

    for syntax, description in rows:
        syn_styled = typer.style(syntax, fg=typer.colors.GREEN, bold=True)
        desc_styled = typer.style(description, dim=True)
        # Right-pad the syntax so descriptions align
        pad = col - len(syntax)
        typer.echo(f"  {syn_styled}{' ' * pad}{desc_styled}")

    _blank()
    _sep()
    typer.echo(
        "  "
        + typer.style("Tip: ", bold=True)
        + "new user? Run  "
        + typer.style("openremap workflow", fg=typer.colors.GREEN, bold=True)
        + "  — it walks you through every step."
    )
    _sep()
    _blank()
