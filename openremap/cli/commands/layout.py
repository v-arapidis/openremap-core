"""
openremap layout <FILE>

Print the flash-layout block map of an ECU binary — where the erased
pages, code, calibration area, and ident blocks start and end.  Data-driven
segmentation, no manufacturer database; kinds are probabilistic labels
with confidence values.

Examples:
    openremap layout ecu.bin
    openremap layout ecu.bin --json
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from openremap.cli.io import load_binary_file
from openremap.core.services.maps.layout import find_ident_blocks, segment


def _read_bin(path: Path) -> bytes:
    """Read + decode a binary file (raw, Intel HEX, or S-Record)."""
    data, _fmt = load_binary_file(path, "Binary")
    return data


def layout(
    file: Path = typer.Argument(
        ...,
        help="ECU binary to segment (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a human-readable table.",
    ),
    min_run: int = typer.Option(
        64,
        "--min-run",
        help="Minimum printable-ASCII run length for ident-block detection (default: 64).",
    ),
) -> None:
    """
    Segment an ECU binary into its flash-layout blocks.

    Kinds (probabilistic labels — see the confidence column):
      erased       one repeated byte (FF/00/… family-specific erase byte)
      code         busy data with no calibration maps
      calibration  dense with high-score maps (RPM×Load tables)
      mixed        no decisive signal — low confidence
      ident        readable ASCII metadata block (exact byte range)
    """
    data = _read_bin(file)
    regions = segment(data)
    ident = find_ident_blocks(data, min_run=min_run)

    if as_json:
        out = {
            "file": str(file),
            "file_size": len(data),
            "regions": [
                {
                    "start": r.start,
                    "end": r.end,
                    "size": r.size,
                    "kind": r.kind,
                    "fill_byte": r.fill_byte,
                    "fill_ratio": r.fill_ratio,
                    "mean_entropy": r.mean_entropy,
                    "tables_high_conf": r.tables_high_conf,
                    "confidence": r.confidence,
                }
                for r in regions
            ],
            "ident_blocks": [
                {"start": b.start, "end": b.end, "size": b.size}
                for b in ident
            ],
        }
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        return

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Flash-Layout Segmentation", bold=True))
    typer.echo(
        typer.style(
            f"  {file.name}  •  {len(data):,} bytes  •  "
            f"{len(regions)} region(s)  •  {len(ident)} ident block(s)",
            dim=True,
        ),
    )
    typer.echo("")
    hdr = typer.style(
        f"  {'Start':>8}  {'End':>8}  {'Size':>9}  {'Kind':>12}  "
        f"{'Fill':>6}  {'Ent':>5}  {'Tbls':>5}  {'Conf':>5}",
        bold=True,
    )
    typer.echo(hdr)
    typer.echo(typer.style("  " + "─" * 74, dim=True))

    colour = {
        "erased": typer.colors.BLUE,
        "code": typer.colors.MAGENTA,
        "calibration": typer.colors.GREEN,
        "ident": typer.colors.CYAN,
        "mixed": typer.colors.YELLOW,
    }
    for r in regions:
        fill = (
            f"0x{r.fill_byte:02X}"
            if r.fill_byte is not None
            else "—"
        )
        typer.echo(
            f"  0x{r.start:06X}  0x{r.end:06X}  {r.size:>8,}  "
            + typer.style(f"{r.kind:>12}", fg=colour[r.kind], bold=True)
            + f"  {fill:>6}  {r.mean_entropy:>5.2f}  {r.tables_high_conf:>5d}  {r.confidence:>5.2f}"
        )

    for b in ident:
        typer.echo(
            f"  0x{b.start:06X}  0x{b.end:06X}  {b.size:>8,}  "
            + typer.style(f"{'ident':>12}", fg=typer.colors.CYAN, bold=True)
            + f"  {'—':>6}  {b.mean_entropy:>5.2f}  {b.tables_high_conf:>5d}  {b.confidence:>5.2f}"
        )
    typer.echo("")
