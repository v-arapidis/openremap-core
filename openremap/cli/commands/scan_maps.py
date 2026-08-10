"""
openremap scan-maps <file>

Scan an ECU binary for plausible calibration map axes and tables
without requiring manufacturer identification.

Examples:
    openremap scan-maps ecu.bin
    openremap scan-maps ecu.bin --top 10
    openremap scan-maps ecu.bin --min-score 0.7
    openremap scan-maps ecu.bin --json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from openremap.core.services.map_hunter import scan_map_axes, scan_map_tables


def _read_bin(path: Path, label: str) -> bytes:
    if path.suffix.lower() not in (".bin", ".ori"):
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin or .ori file.",
                fg=typer.colors.RED, bold=True,
            ), err=True,
        )
        raise typer.Exit(code=1)
    try:
        data = path.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(f"Error reading file: {exc}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=1)
    if not data:
        typer.echo(
            typer.style(f"Error: '{path.name}' is empty.", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=1)
    return data


def scan_maps(
    file: Path = typer.Argument(
        ...,
        help="ECU binary file to scan (.bin or .ori).",
        exists=True, file_okay=True, dir_okay=False, readable=True, resolve_path=True,
    ),
    top: int = typer.Option(20, "--top", "-n", help="Number of top-scoring tables to show (default: 20)."),
    min_score: float = typer.Option(0.75, "--min-score", "-s", help="Minimum table score in [0, 1] (default: 0.75)."),
    region: str | None = typer.Option(
        None, "--region", "-r",
        help="Restrict scanning to a byte range: '0xSTART-0xEND' or 'START-END' (e.g. '0x10000-0x80000').",
        metavar="RANGE",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """
    Scan a binary for plausible calibration map axes and 2D tables.

    This is a structural scan — it looks for monotonically increasing 16-bit
    sequences (RPM/load breakpoints) and the rectangular data blocks that
    follow them.  No manufacturer identification is needed; it works on any
    binary regardless of ECU family.

    Useful for:
      - Checking whether a binary contains genuine calibration data.
        A genuine ECU binary typically has hundreds or thousands of axes.
        A corrupt / encrypted / non-ECU file has almost none.
      - Discovering candidate map locations in unsupported ECUs.
        Feed the offsets into WinOLS or ECM Titanium as starting points.

    The default --min-score of 0.75 filters out most coincidental patterns
    (code sections, pointer tables, lookup arrays).  Lower it to 0.55 for
    exhaustive scanning of unsupported ECUs; raise to 0.85 for high-confidence
    calibration maps only.

    Run  openremap identify <file>  first to see manufacturer and SW info;
    run this after to see the map structure.
    """
    data = _read_bin(file, "Binary")

    # Parse --region
    region_slice: slice | None = None
    if region is not None:
        try:
            r = region.strip()
            if r.startswith("0x") or r.startswith("0X"):
                r = r[2:]
            parts = r.replace("-", " ").replace("..", " ").split()
            if len(parts) >= 2:
                start = int(parts[0], 16 if any(c.lower() in 'abcdef' for c in parts[0]) else 0)
                end = int(parts[-1], 16 if any(c.lower() in 'abcdef' for c in parts[-1]) else 0)
                region_slice = slice(start, end)
            else:
                typer.echo(typer.style("Error: --region must be 'START-END' or '0xSTART-0xEND'.", fg=typer.colors.RED), err=True)
                raise typer.Exit(code=1)
        except (ValueError, IndexError):
            typer.echo(typer.style("Error: invalid --region format. Use '0xSTART-0xEND'.", fg=typer.colors.RED), err=True)
            raise typer.Exit(code=1)

    axes = scan_map_axes(data, region=region_slice)
    tables = scan_map_tables(data, region=region_slice, axes=axes, min_score=min_score)

    if as_json:
        result = {
            "file": str(file),
            "file_size": len(data),
            "axes_count": len(axes),
            "tables_count": len(tables),
            "axes": [
                {"offset": a.offset, "length": a.length, "byte_order": a.byte_order,
                 "values": list(a.values[:16])}
                for a in axes[:200]  # cap to keep JSON reasonable
            ],
            "tables": [
                {"offset": t.offset, "cols": t.cols, "rows": t.rows,
                 "cell_width": t.cell_width, "byte_order": t.byte_order,
                 "x_axis_offset": t.x_axis_offset, "y_axis_offset": t.y_axis_offset,
                 "score": t.score}
                for t in tables[:top]
            ],
        }
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # ── Human-readable output ────────────────────────────────────────────
    typer.echo("")
    typer.echo(
        typer.style(f"  {file.name}", fg=typer.colors.CYAN, bold=True)
    )
    typer.echo("")

    # Health signal
    axes_count = len(axes)
    if axes_count >= 1000:
        health = typer.style("  ✓  Genuine calibration binary", fg=typer.colors.GREEN, bold=True)
    elif axes_count >= 100:
        health = typer.style("  ⚠  Few axes — possibly corrupted or trimmed", fg=typer.colors.YELLOW, bold=True)
    else:
        health = typer.style("  ✗  Very few axes — likely encrypted, non-ECU, or empty", fg=typer.colors.RED, bold=True)

    typer.echo(health)
    typer.echo(f"  {axes_count:,} axes  •  {len(tables):,} tables  •  {len(data):,} bytes")
    typer.echo("")

    if not tables:
        typer.echo(
            typer.style("  No tables found with the current min-score threshold.", dim=True)
        )
        typer.echo("")
        return

    # Table listing
    # Header
    hdr = typer.style(
        f"  {'Offset':>10}  {'Dim':>8}  {'Cells':>6}  {'Score':>7}  "
        f"{'X Axis':>10}  {'Y Axis':>10}",
        bold=True,
    )
    typer.echo(hdr)
    typer.echo(typer.style("  " + "─" * 62, dim=True))

    for t in tables[:top]:
        dim = f"{t.cols}×{t.rows}"
        cells = f"{'u8' if t.cell_width == 1 else 'u16'} {t.byte_order[:3].upper()}"
        score_colour = (
            typer.colors.GREEN if t.score >= 0.85
            else typer.colors.YELLOW if t.score >= 0.70
            else typer.colors.WHITE
        )
        y_axis = f"0x{t.y_axis_offset:X}" if t.y_axis_offset is not None else "—"

        typer.echo(
            f"  0x{t.offset:08X}  {dim:>8}  {cells:>6}  "
            + typer.style(f"{t.score:.3f}", fg=score_colour)
            + f"  0x{t.x_axis_offset:08X}  {y_axis}"
        )

    typer.echo("")

    if len(tables) > top:
        typer.echo(
            typer.style(
                f"  … and {len(tables) - top} more.  Use --top {len(tables)} to see all, "
                "or --min-score 0.8 to filter.",
                dim=True,
            )
        )
        typer.echo("")
