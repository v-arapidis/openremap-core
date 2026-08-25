"""
openremap health [FILE]

One-shot calibration health check for an ECU binary.

Runs every analysis layer (identity, checksums, axis sanity, map-count
envelope, erased-block layout, VIN duplication) and reports each concern
as ok / warn / fail / skip.  Exit code 0 = healthy, 1 = at least one
check failed (CI gate).

Examples:
    openremap health ecu.bin
    openremap health ecu.bin --json
    openremap health ecu.bin --json --output report.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from openremap.cli.io import load_binary_file
from openremap.core.services.health import health_report

_STATUS_COLOURS = {
    "ok": typer.colors.GREEN,
    "warn": typer.colors.YELLOW,
    "fail": typer.colors.RED,
    "skip": typer.colors.CYAN,
}


def health_cmd(
    file: Path = typer.Argument(
        ...,
        help="ECU binary to check (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a table.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Write the report to this file (JSON only, requires --json).",
    ),
) -> None:
    """One-shot calibration health check (CI-gateable)."""
    data, _fmt = load_binary_file(file, "Binary")

    report = health_report(data, file.name)

    if as_json:
        payload = {
            "file": str(file),
            "file_size": report.file_size,
            "family": report.family,
            "manufacturer": report.manufacturer,
            "confidence_tier": report.confidence_tier,
            "healthy": report.healthy,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "details": c.details,
                }
                for c in report.checks
            ],
        }
        content = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        if output is not None:
            try:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content)
            except OSError as exc:
                typer.echo(
                    typer.style(
                        f"Error: cannot write '{output}': {exc}",
                        fg=typer.colors.RED, bold=True,
                    ),
                    err=True,
                )
                raise typer.Exit(code=1)
        else:
            typer.echo(content)
        raise typer.Exit(code=0 if report.healthy else 1)

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Calibration Health", bold=True))
    ident = report.family or "Unknown"
    status_colour = typer.colors.GREEN if report.healthy else typer.colors.RED
    typer.echo(
        typer.style(
            f"  {file.name}  •  {report.file_size:,} bytes  •  {ident}"
            f"  •  {'HEALTHY' if report.healthy else 'ISSUES FOUND'}",
            fg=status_colour, bold=True,
        )
    )
    typer.echo("")
    for c in report.checks:
        colour = _STATUS_COLOURS.get(c.status, typer.colors.WHITE)
        typer.echo(
            f"  {c.name:<14}"
            + typer.style(f"{c.status.upper():<5}", fg=colour, bold=True)
            + f" {c.message}"
        )
        for d in c.details:
            typer.echo(typer.style(f"      {d}", fg=typer.colors.BRIGHT_BLACK))
    typer.echo("")

    if not report.healthy:
        raise typer.Exit(code=1)
