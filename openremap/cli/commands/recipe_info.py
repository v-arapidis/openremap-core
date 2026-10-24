"""
openremap recipe-info <file.remap>

Say what a recipe declares — without applying it: schema, how many instructions
it carries, what it needs to be applied to (its identity floor), and which files
it was cooked from.

This is the read side of a recipe. Values come from the file itself and are
checked by the same reader the apply engine uses, so a recipe that looks broken
here would be refused there, with the same rule id.

Examples:
    openremap recipe-info stage1.remap
    openremap recipe-info stage1.remap --json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from openremap.api import call
from openremap.api.errors import GuardError

__all__ = ["recipe_info_cmd"]


def _print_info(info: dict, path: Path) -> None:
    contract = info.get("contract") or {}
    source = info.get("source") or {}
    tune = info.get("tune") or {}
    producer = info.get("producer") or {}
    stats = info.get("statistics") or {}

    typer.echo()
    typer.echo(typer.style(f"  {path.name}", bold=True))
    typer.echo(typer.style("  " + "─" * 62, fg=typer.colors.BRIGHT_BLACK))
    typer.echo(f"  SCHEMA        {info.get('schema')}  ({producer.get('command') or '?'})")
    typer.echo(f"  CONTENT       {info.get('instructions')} instructions · "
               f"{int(info.get('bytes_written') or 0):,} bytes")
    typer.echo(f"  PORTABILITY   identity_floor {contract.get('identity_floor')} · "
               f"anchors_unique {contract.get('anchors_unique')} · "
               f"sections {', '.join(contract.get('sections') or []) or 'none'}")
    if source.get("filename"):
        typer.echo(f"  FROM          {source.get('filename')}  "
                   f"({source.get('size'):,} B · {source.get('match_key') or 'unidentified'!s})")
    if tune.get("filename"):
        typer.echo(f"  TO            {tune.get('filename')}")
    if stats:
        typer.echo(f"  CHANGED       {int(stats.get('total_bytes_changed') or 0):,} bytes "
                   f"({float(stats.get('percentage_changed') or 0):.2f} %) · "
                   f"{stats.get('single_byte_changes', 0)} single / "
                   f"{stats.get('multi_byte_changes', 0)} multi-byte")
    typer.echo(f"  PRODUCED      {producer.get('engine')} {producer.get('version')} "
               f"· {producer.get('created_at') or '?'}")


def recipe_info_cmd(
    file: Annotated[Path, typer.Argument(help="Path to a .remap recipe.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the facts as JSON.")
    ] = False,
) -> None:
    """Read a recipe and report what it declares (no target needed)."""
    try:
        info = call("recipe_info", {"path": str(file)})
    except GuardError as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED, bold=True), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(info, indent=2))
        return
    _print_info(info, file)
