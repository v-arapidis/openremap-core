"""
openremap convert <INPUT> [-o OUTPUT] [--format auto|ihex|srec|bin] [--json]

Normalise an ECU binary image to a flat raw binary: real Intel HEX and
Motorola S-Record text files are parsed (addresses + per-record checksums
validated) and written as plain bytes; raw dumps pass through unchanged.

Examples:
    openremap convert boot.hex -o boot.bin
    openremap convert flash.s19 --json
    openremap convert weird.bin --format bin -o out.bin     # force raw
    openremap convert corrupt.hex --format ihex             # strict parse
"""

import json as stdjson
from pathlib import Path
from typing import Optional

import typer

from openremap.core.services.convert import DecodeResult, decode_image

_FMT_NAMES = {"ihex": "Intel HEX", "srec": "Motorola S-Record", "binary": "raw binary"}
_FMT_CHOICES = ("auto", "ihex", "srec", "bin")


def convert(
    input: Path = typer.Argument(
        ...,
        help="Input file: Intel HEX, S-Record, or raw binary (.bin/.ori/.hex/.s19/.srec/.mot).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the flat binary here (default: <input stem>.bin next to the input).",
        writable=True,
        resolve_path=True,
    ),
    format: str = typer.Option(
        "auto",
        "--format",
        help="auto (sniff content) | ihex | srec | bin (force raw).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output the summary as JSON.",
    ),
) -> None:
    """Normalise an ECU binary image (Intel HEX / S-Record / raw) to flat bytes."""
    if format not in _FMT_CHOICES:
        typer.echo(
            typer.style(
                f"Error: --format must be one of: {', '.join(_FMT_CHOICES)}.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        raw = input.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error reading '{input.name}': {exc}", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if not raw:
        typer.echo(
            typer.style(
                f"Error: '{input.name}' is empty.", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    force = None if format == "auto" else format
    try:
        result: DecodeResult = decode_image(raw, force=force)
    except ValueError as exc:
        typer.echo(
            typer.style(
                f"Error: '{input.name}' is not a valid binary image: {exc}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    out_path = output or (input.parent / f"{input.stem}.bin")
    try:
        out_path.write_bytes(result.data)
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error: could not write '{out_path}': {exc}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    for warning in result.warnings:
        typer.echo(typer.style(f"  ⚠  {warning}", fg=typer.colors.YELLOW), err=True)

    if as_json:
        payload = {
            "input": str(input),
            "format": result.format,
            "format_name": _FMT_NAMES[result.format],
            "output": str(out_path),
            "size": len(result.data),
            "address_min": result.address_min,
            "address_max": result.address_max,
            "segments": result.segments,
            "warnings": result.warnings,
        }
        typer.echo(
            stdjson.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        )
        return

    typer.echo(
        f"  {_FMT_NAMES[result.format]:<20}  {len(result.data):,} bytes"
        f"{'  @ 0x%X-0x%X' % (result.address_min, result.address_max - 1) if result.address_min is not None else ''}"
    )
    typer.echo(f"  Saved to {out_path}")
