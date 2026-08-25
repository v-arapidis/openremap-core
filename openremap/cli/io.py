"""
CLI input-boundary helpers: read + decode binary image files.

Single-file commands use :func:`load_binary_file` (reads, decodes, and
exits 1 with a styled error on any problem, matching the old per-command
``_read_bin`` behaviour — including the "empty file" contract that tests
rely on).  Batch loops (``scan``, ``scan-maps`` directory mode, TUI batch
scan) keep their own per-file error handling and call
:func:`openremap.core.services.convert.decode_image` directly after their
own ``read_bytes()``, so a decode failure is one row, never an abort.
"""

import typer
from pathlib import Path

from openremap.core.services.convert import DecodeResult, decode_image

#: Advisory extension set for ECU binary inputs.  The real gate is content
#: sniffing (:func:`decode_image`) — the extension only affects the
#: user-facing error message and the scan ``trash`` classifier.  ``.hex`` is
#: kept because Subaru (Denso/Hitachi) dumps conventionally ship as raw
#: binaries named ".hex" (RomRaider convention) — not Intel HEX text.
BINARY_EXTENSIONS = (".bin", ".ori", ".hex", ".s19", ".srec", ".mot")

_EXT_TEXT = ", ".join(BINARY_EXTENSIONS)

#: Friendly names for the container format code returned by decode_image.
CONTAINER_NAMES: dict[str, str] = {
    "ihex": "Intel HEX",
    "srec": "Motorola S-Record",
    "binary": "raw binary",
}


def load_binary_file(path: Path, label: str) -> tuple[bytes, str]:
    """Read + decode a binary input file; exit 1 with a clear error on failure.

    ``path`` is a :class:`pathlib.Path` (as passed by the CLI commands).
    Returns ``(data, format)`` where ``format`` is ``"ihex"`` / ``"srec"`` /
    ``"binary"``.  Decode warnings (e.g. gap filling) are printed to stderr.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error reading {label} file: {exc}", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if not raw:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' is empty.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        result: DecodeResult = decode_image(raw)
    except ValueError as exc:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' is not a valid binary image: {exc}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    for warning in result.warnings:
        typer.echo(
            typer.style(f"  ⚠  {warning}", fg=typer.colors.YELLOW),
            err=True,
        )
    return result.data, result.format
