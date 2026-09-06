"""
openremap scan-vins <FILE>

Locate VIN candidates in an ECU binary and score them.

Examples:
    openremap scan-vins ecu.bin
    openremap scan-vins ecu.bin --json
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from openremap.api.commands.scan_vins import scan_vins_binary
from openremap.core.cli.io import load_binary_file


def scan_vins_cmd(
    file: Path = typer.Argument(
        ...,
        help="ECU binary to scan (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    min_confidence: float = typer.Option(
        0.4,
        "--min-confidence",
        help="Only show candidates with confidence >= this value (0.0-1.0, default: 0.4).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a table.",
    ),
) -> None:
    """
    Scan an ECU binary for VIN candidates.

    Candidates are scored on structural evidence — WMI (manufacturer)
    prefix, ISO 3779 check digit, model-year character, numeric serial
    tail, ident-block location, and mirror consensus.  Confidence is a
    probability-style score (0.0-1.0), NEVER a boolean claim: ECU files
    are full of VIN-shaped serials and calibration IDs.
    """
    data, _fmt = load_binary_file(file, "Binary")

    result = scan_vins_binary(data, file.name, min_confidence=min_confidence)
    candidates = result["candidates"]

    if as_json:
        result["file"] = str(file)
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — VIN Scan", bold=True))
    typer.echo(
        typer.style(
            f"  {file.name}  •  {len(data):,} bytes  •  "
            f"{len(candidates)} candidate(s) ≥ {min_confidence}",
            dim=True,
        ),
    )
    typer.echo("")

    if not candidates:
        typer.echo("  No VIN candidates found at this confidence level.")
        typer.echo("")
        return

    typer.echo(
        typer.style(
            f"  {'Offset':>8}  {'VIN':<19}  {'Conf':>5}  Evidence",
            bold=True,
        )
    )
    typer.echo(typer.style("  " + "─" * 66, dim=True))
    for c in candidates:
        colour = (
            typer.colors.GREEN
            if c["confidence"] >= 0.6
            else typer.colors.YELLOW
        )
        decoded_suffix = ""
        if c["decoded"]:
            country = c["country"]
            years = c["years"]
            decoded_suffix = (
                f"  — {c['manufacturer']}"
                + (f", {country}" if country else "")
                + (f", {years[0]}" if years else "")
                + " (decoded, unverified)"
            )
        typer.echo(
            f"  0x{c['offset']:06X}  "
            + typer.style(f"{c['vin']:<19}", fg=colour, bold=c["confidence"] >= 0.6)
            + f"  {c['confidence']:>5.2f}  {', '.join(c['evidence'])}"
            + typer.style(decoded_suffix, dim=True)
        )
    typer.echo("")
