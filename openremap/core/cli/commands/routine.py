"""
openremap routine <file> <offset>

Read a code routine as pseudo-code (no Ghidra needed).

Decodes the instruction stream around a file offset and renders each
instruction as one readable line — the "phrasebook" pseudo-decompiler.
The decoder is picked from the identified ECU family (Rust C166, or
capstone for TriCore/SH/x86/M680X/68K/PPC), with an optional ``--arch``
override.

Examples:
    openremap routine ecu.bin 0x50000
    openremap routine ecu.bin 0x50000 --arch c166 --after 120
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from openremap.api.commands.routine import routine_binary
from openremap.core.cli.io import load_binary_file


def _parse_offset(text: str) -> int:
    try:
        return int(text, 0)  # accepts 0x… / 0o… / decimal
    except ValueError:
        raise typer.BadParameter(
            f"invalid offset {text!r} — use hex (0x…) or decimal"
        )


def routine(
    file: Path = typer.Argument(
        ...,
        help="ECU binary file (.bin, .ori, .hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    offset: str = typer.Argument(
        ...,
        help="File offset to disassemble at (hex 0x… or decimal).",
    ),
    arch: Optional[str] = typer.Option(
        None,
        "--arch",
        help="Decoder override (c166|tricore|sh|x86|m680x|m68k|ppc); "
        "auto-detected from the family when omitted.",
    ),
    before: int = typer.Option(
        8, "--before", help="Instructions to show before the target."
    ),
    after: int = typer.Option(
        60, "--after", help="Instructions to show after the target."
    ),
) -> None:
    """
    Read a code routine as readable pseudo-code.

    Each instruction renders as one line (offset, mnemonic, operands); the
    instruction nearest the target offset is marked '>>'.  This is a
    phrasebook, not a full decompiler — no variable renaming or loop
    reconstruction.
    """
    off = _parse_offset(offset)
    data, _fmt = load_binary_file(file, "Binary")

    try:
        result = routine_binary(
            data, file.name, off, arch=arch, before=before, after=after
        )
    except ValueError as exc:
        typer.echo(typer.style(str(exc), fg=typer.colors.RED, bold=True), err=True)
        raise typer.Exit(code=1)

    key = result["arch"]
    lines = result["lines"]
    typer.echo(f"\n  {file.name}  @ 0x{off:X}  ({key})\n")
    for line in lines:
        if line.startswith(">>"):
            typer.echo(typer.style(line, fg=typer.colors.CYAN, bold=True))
        else:
            typer.echo(line)
