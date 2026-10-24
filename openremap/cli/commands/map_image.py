"""
openremap map-image <binary> [--offset 0x3D53F4]

Draw one detected calibration table as an SVG picture: every cell's number,
coloured by value, with the breakpoints that are actually stored in the binary,
a colour scale, and a footer saying what the picture does not claim.

Why a file and not the terminal: a terminal has no pixels. Character cells,
fonts, colour depth and aspect ratio are outside our control, so a map drawn in
characters can look wrong on a different machine while claiming to be data. SVG
is text, needs no image library, scales, prints, and can be opened in a browser.

Examples:
    openremap map-image ecu.bin                       # the highest-scoring table
    openremap map-image ecu.bin --top 3               # fourth-highest? no: the 3rd
    openremap map-image ecu.bin --offset 0x3D53F4     # one specific table
    openremap map-image ecu.bin --min-score 0.5       # loosen the scan first
    openremap map-image ecu.bin -o /tmp/map.svg
    openremap map-image ecu.bin --no-values           # big tables: colour only
    openremap map-image ecu.bin --list                # what is available
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from openremap.api.commands.map_image import map_image as _map_image
from openremap.api.commands.scan_maps import scan_maps as _scan_maps
from openremap.cli.io import load_binary_file

__all__ = ["map_image_cmd"]

_SCAN_TOP = 2000


def _offset_value(text: str) -> int:
    """Parse ``0x3D53F4`` or ``4019188``."""
    try:
        return int(text, 0)
    except ValueError as exc:  # pragma: no cover - Typer reports this first
        raise typer.BadParameter(f"not an offset: {text}") from exc


def _list_tables(tables: list[dict], min_score: float) -> None:
    typer.echo(
        typer.style(
            f"{len(tables)} tables at min score {min_score:.2f} (top {_SCAN_TOP} shown)",
            fg=typer.colors.CYAN,
            bold=True,
        )
    )
    for index, table in enumerate(tables):
        typer.echo(
            f"  [{index:>3}] 0x{int(table.get('offset') or 0):06X}  "
            f"{table.get('cols')}x{table.get('rows')}  "
            f"{table.get('cell_width')} B  "
            f"score {float(table.get('score') or 0):.2f}  "
            f"{table.get('label') or 'unknown'}"
        )
    typer.echo("\nDraw one with: openremap map-image <binary> --offset 0x<offset>")


def map_image_cmd(
    file: Annotated[Path, typer.Argument(help="ECU binary to read the table from.")],
    offset: Annotated[
        str | None,
        typer.Option("--offset", help="Table offset to draw, in hex (0x...) or decimal."),
    ] = None,
    top: Annotated[
        int,
        typer.Option("--top", help="Index into the scan result (0 = highest scoring)."),
    ] = 0,
    min_score: Annotated[
        float,
        typer.Option("--min-score", help="Scanner threshold used to find the table."),
    ] = 0.85,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write the SVG (default: next to the dump)."),
    ] = None,
    width: Annotated[
        int,
        typer.Option("--width", help="Minimum image width in pixels."),
    ] = 960,
    values: Annotated[
        bool,
        typer.Option("--values/--no-values", help="Write each cell's number inside the cell."),
    ] = True,
    list_tables: Annotated[
        bool,
        typer.Option("--list", help="Only list the tables found, then exit."),
    ] = False,
) -> None:
    """Draw one detected table as an SVG picture (cells + breakpoints + scale)."""
    data_exists = file.exists()
    if not data_exists:
        typer.echo(
            typer.style(f"Error: no such file: {file}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=1)

    scanned = _scan_maps(
        {
            "path": str(file),
            "min_score": float(min_score),
            "top": _SCAN_TOP,
        }
    )
    tables = list(scanned.get("tables") or [])
    if not tables:
        typer.echo(
            typer.style(
                f"No tables found at min score {min_score:.2f}. "
                "Lower it with --min-score (0.55 finds far more, and much more noise).",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if list_tables:
        _list_tables(tables, float(min_score))
        return

    if offset is not None:
        wanted = _offset_value(offset)
        matches = [t for t in tables if int(t.get("offset") or 0) == wanted]
        if not matches:
            nearest = min(
                tables, key=lambda t: abs(int(t.get("offset") or 0) - wanted)
            )
            typer.echo(
                typer.style(
                    f"No table at 0x{wanted:06X}. Nearest is "
                    f"0x{int(nearest.get('offset') or 0):06X} — "
                    "run with --list to see them all.",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
        table = matches[0]
    else:
        if not 0 <= top < len(tables):
            typer.echo(
                typer.style(
                    f"--top {top} is out of range: {len(tables)} tables found.",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
        table = tables[top]

    # The binary has already been read once by the scan; the picture reads it
    # again through the same gate, so a picture can never disagree with a scan.
    load_binary_file(file, "ECU")  # validates container/extensions like every command
    picture = _map_image(
        {
            "path": str(file),
            "table": table,
            "name": file.name,
            "include_values": bool(values),
            "width": int(width),
        }
    )

    target = output or file.with_name(
        f"{file.stem}_map_0x{int(table.get('offset') or 0):06X}.svg"
    )
    target.write_text(picture["svg"], encoding="utf-8")

    summary = picture["summary"]
    axes = picture["axes"]
    typer.echo()
    typer.echo(typer.style(f"  {file.name}", fg=typer.colors.WHITE, bold=True))
    typer.echo("  " + "─" * 62)
    typer.echo(f"  TABLE         0x{int(table.get('offset') or 0):06X}  "
               f"{table.get('cols')} x {table.get('rows')} cells  "
               f"{table.get('cell_width')} B")
    typer.echo(f"  SCORE         {float(table.get('score') or 0):.2f}  "
               f"label {picture.get('label') or 'unknown'}")
    typer.echo(f"  VALUES        min {summary['min']} · max {summary['max']} · "
               f"mean {float(summary['mean']):.1f} · distinct {summary['distinct']}")
    typer.echo(
        "  BREAKPOINTS   "
        + ("read from the binary" if axes["source"] == "stored" else "none found — using cell indices")
    )
    typer.echo(f"  WRITTEN       {target}")
    typer.echo(typer.style("\n  Open it with: xdg-open " + str(target), fg=typer.colors.CYAN))
