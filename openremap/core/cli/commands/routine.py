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

from openremap.core.arch import arch_for_family
from openremap.core.arch.pseudocode import render_routine
from openremap.core.cli.io import load_binary_file
from openremap.core.services.identify.identifier import identify_ecu

_ARCH_KEYS = ("c166", "tricore", "sh", "x86", "m680x", "m68k", "ppc")


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

    key = arch
    if key is None:
        ident = identify_ecu(data=data, filename=file.name)
        info = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
        if info is None:
            fam = ident.get("ecu_family") or "unknown"
            typer.echo(
                typer.style(
                    f"No decoder for family '{fam}' — pass --arch to force one "
                    f"(supported: {', '.join(_ARCH_KEYS)}).",
                    fg=typer.colors.YELLOW,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
        key = info[0]

    if key not in _ARCH_KEYS:
        typer.echo(
            typer.style(f"Unsupported arch {key!r}.", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=1)

    lines = render_routine(data, off, arch=key, before=before, after=after)
    typer.echo(f"\n  {file.name}  @ 0x{off:X}  ({key})\n")
    for line in lines:
        if line.startswith(">>"):
            typer.echo(typer.style(line, fg=typer.colors.CYAN, bold=True))
        else:
            typer.echo(line)
