"""
``openremap synth`` — generate a synthetic ECU-like binary from scratch.

No ECU dump is needed and none is touched: the file is built from a seed
(sector layout, calibration axes, smooth map surfaces).  That makes it safe to
commit as a test fixture, ship as a demo, or attach to a bug report — and it is
the input format of the golden corpus (``tests/corpus/``).

The generated file carries no manufacturer fingerprints, so ``identify``
reports ``Unknown`` for it.  That is intentional, not a limitation to fix.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import typer

from openremap.api import call
from openremap.api.errors import ApiError

DEFAULT_LAYOUT = "code,calibration,erased,code"


def synth(
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Where to write the generated binary.",
    ),
    size: int = typer.Option(
        256 * 1024,
        "--size",
        help="Total size in bytes (default 256 KB — real ECUs are 32 KB to 4 MB).",
    ),
    maps: int = typer.Option(
        1,
        "--maps",
        help="How many calibration tables to plant (shapes cycle a fixed palette).",
    ),
    seed: int = typer.Option(
        7,
        "--seed",
        help="Seeds the background noise: same parameters + seed = same file, always.",
    ),
    cell_width: int = typer.Option(
        2,
        "--cell-width",
        help="Cell width in bytes: 2 (16-bit cells) or 1 (8-bit).",
    ),
    byte_order: str = typer.Option(
        "little",
        "--byte-order",
        help="Byte order of axes and cells: little or big.",
    ),
    layout: str = typer.Option(
        DEFAULT_LAYOUT,
        "--layout",
        help="Comma-separated sector kinds: code, calibration, erased.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the planted ground truth as JSON (the binary is still written).",
    ),
) -> None:
    """Write a synthetic ECU-like binary (no OEM data involved).

    Examples:
        openremap synth -o demo.bin
        openremap synth -o demo.bin --size 1048576 --maps 8 --seed 42
        openremap synth -o pair-stock.bin --json
    """
    kinds = [k.strip() for k in layout.split(",") if k.strip()]
    try:
        result = call(
            "generate_dump",
            {
                "size": size,
                "maps": maps,
                "seed": seed,
                "cell_width": cell_width,
                "byte_order": byte_order,
                "layout": kinds,
            },
        )
    except ApiError as exc:
        typer.echo(
            typer.style(f"Error: {exc}", fg=typer.colors.RED, bold=True), err=True
        )
        raise typer.Exit(code=1)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(result["data_b64"]))
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error: could not write '{output}': {exc}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if as_json:
        payload = {k: v for k, v in result.items() if k != "data_b64"}
        payload["path"] = str(output)
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    typer.echo("")
    typer.echo(
        typer.style(f"  {output}", fg=typer.colors.BRIGHT_WHITE, bold=True)
        + typer.style("  (synthetic — no OEM data)", dim=True)
    )
    typer.echo(f"  {'Size':<18}{result['size']:,} bytes")
    typer.echo(f"  {'SHA-256':<18}{result['sha256']}")
    sectors = result["sectors"]
    typer.echo(
        f"  {'Sectors':<18}"
        + ", ".join(
            f"{s['kind']} {s['start']:#x}-{s['end']:#x}" for s in sectors
        )
    )
    planted = result["maps"]
    typer.echo(f"  {'Planted maps':<18}{len(planted)}")
    for m in planted:
        typer.echo(
            f"      map{m['map_id']}  {m['cols']}x{m['rows']} "
            f"{'u8' if m['cell_width'] == 1 else 'u16'} {m['byte_order']}  "
            f"data @ {m['offset']:#x}"
        )
    typer.echo(
        typer.style(
            "  identify reports Unknown for generated files — by design "
            "(no manufacturer fingerprints).",
            dim=True,
        )
    )
    typer.echo("")
