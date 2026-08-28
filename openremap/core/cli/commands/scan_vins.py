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

from openremap.core.cli.io import load_binary_file
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.services.vin_decode import decode_vin


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

    hits = scan_vins(data, min_confidence=min_confidence)
    decoded = [decode_vin(h.vin) for h in hits]

    if as_json:
        out = {
            "file": str(file),
            "file_size": len(data),
            "candidates": [
                {
                    "offset": h.offset,
                    "vin": h.vin,
                    "confidence": h.confidence,
                    "evidence": h.evidence,
                    "mirror_count": h.mirror_count,
                    "manufacturer": d.manufacturer,
                    "region": d.region,
                    "country": d.country,
                    "years": d.years,
                    "checksum_valid": d.checksum_valid,
                    "decoded": d.decoded,
                }
                for h, d in zip(hits, decoded)
            ],
        }
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
        return

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — VIN Scan", bold=True))
    typer.echo(
        typer.style(
            f"  {file.name}  •  {len(data):,} bytes  •  "
            f"{len(hits)} candidate(s) ≥ {min_confidence}",
            dim=True,
        ),
    )
    typer.echo("")

    if not hits:
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
    for h, d in zip(hits, decoded):
        colour = (
            typer.colors.GREEN
            if h.confidence >= 0.6
            else typer.colors.YELLOW
        )
        decoded_suffix = ""
        if d.decoded:
            decoded_suffix = (
                f"  — {d.manufacturer}"
                f"{f', {d.country}' if d.country else ''}"
                f"{f', {d.years[0]}' if d.years else ''} (decoded, unverified)"
            )
        typer.echo(
            f"  0x{h.offset:06X}  "
            + typer.style(f"{h.vin:<19}", fg=colour, bold=h.confidence >= 0.6)
            + f"  {h.confidence:>5.2f}  {', '.join(h.evidence)}"
            + typer.style(decoded_suffix, dim=True)
        )
    typer.echo("")
