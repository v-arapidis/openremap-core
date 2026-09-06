"""
openremap identify <file>

Identify a single ECU binary and print its metadata.

Examples:
    openremap identify ecu.bin
    openremap identify ecu.bin --json
    openremap identify ecu.bin --json --output result.json
    openremap identify ecu.rom       # non-.bin/.ori extensions accepted with a warning
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer

from openremap.api.commands.identify import identify_binary
from openremap.core.cli.io import CONTAINER_NAMES, load_binary_file
from openremap.core.services.identify.confidence import ConfidenceResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LABELS: list[tuple[str, str]] = [
    ("container", "Container"),
    ("manufacturer", "Manufacturer"),
    ("ecu_family", "ECU Family"),
    ("ecu_variant", "ECU Variant"),
    ("software_version", "Software Version"),
    ("hardware_number", "Hardware Number"),
    ("calibration_id", "Calibration ID"),
    ("match_key", "Match Key"),
    ("ecu_endian", "Byte Order"),
    ("ecu_cell_bytes", "Cell Size"),
    ("file_size", "File Size"),
    ("sha256", "SHA-256"),
]

_TIER_COLOURS: dict[str, str] = {
    "High": typer.colors.GREEN,
    "Medium": typer.colors.YELLOW,
    "Low": typer.colors.MAGENTA,
    "Suspicious": typer.colors.RED,
    "Unknown": typer.colors.CYAN,
}


def _format_confidence_inline(confidence: ConfidenceResult) -> str:
    """Render the confidence result as a compact coloured string for the table."""
    colour = _TIER_COLOURS.get(confidence.tier, typer.colors.WHITE)
    tier_str = typer.style(confidence.tier.upper(), fg=colour, bold=True)
    summary = confidence.rationale_summary(max_signals=3)
    summary_str = typer.style(f"  {summary}", dim=True) if summary else ""
    return f"{tier_str}{summary_str}"


def _format_confidence_warnings(warnings: list[str], indent: str = "  ") -> str:
    """Return a formatted warnings block, or an empty string when there are none."""
    if not warnings:
        return ""
    lines = []
    for w in warnings:
        lines.append(indent + typer.style("⚠  " + w, fg=typer.colors.RED, bold=True))
    return "\n".join(lines)


def _format_table(result: dict) -> str:
    """Render the identity dict as a two-column aligned table."""
    rows: list[tuple[str, str]] = []
    for key, label in _LABELS:
        value = result.get(key)
        if key == "file_size" and value is not None:
            display = f"{value:,} bytes"
        elif key == "ecu_cell_bytes" and value is not None:
            display = f"{value * 8}-bit"
        elif key == "ecu_endian" and value is not None:
            display = f"{value}-endian"
        elif value is None:
            display = typer.style("unknown", fg=typer.colors.YELLOW)
        else:
            display = str(value)
        rows.append((label, display))

    col_width = max(len(label) for label, _ in rows)
    lines = []
    for label, value in rows:
        lines.append(f"  {label:<{col_width}}  {value}")
    return "\n".join(lines)


def _write_output(content: str, output: Optional[Path]) -> None:
    """Write content to a file (creating parent directories) or stdout."""
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
        except OSError as exc:
            typer.echo(
                typer.style(
                    f"Error: could not write output to '{output}': {exc}",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Saved to {output}")
    else:
        typer.echo(content)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def identify(
    file: Path = typer.Argument(
        ...,
        help="ECU binary file to identify (.bin, .ori, or .hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output result as JSON instead of a human-readable table.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save the result to a file instead of printing to stdout.",
        writable=True,
        resolve_path=True,
    ),
) -> None:
    """
    Identify a single ECU binary.

    Prints manufacturer, ECU family, software version, hardware number,
    calibration ID, match key, file size, SHA-256 hash, and a confidence
    assessment of how reliably the binary was identified.
    """
    suffix = file.suffix.lower()
    if suffix not in (".bin", ".ori", ".hex", ".s19", ".srec", ".mot"):
        typer.echo(
            typer.style(
                f"  ⚠  Unrecognised extension '{file.suffix}' — proceeding anyway. "
                "Expected .bin, .ori, .hex, .s19, .srec, or .mot.",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )

    data, fmt_code = load_binary_file(file, "Binary")
    container = CONTAINER_NAMES.get(fmt_code, fmt_code)

    try:
        result = identify_binary(data, file.name, container)
    except Exception as exc:
        typer.echo(
            typer.style(
                f"Identification failed: {exc}", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if as_json:
        content = json.dumps(result, indent=2)
        _write_output(content, output)
        return

    # --- Table output ---
    header = typer.style(f"\n  {file.name}", fg=typer.colors.CYAN, bold=True)

    identified = result.get("ecu_family") is not None
    status_colour = typer.colors.GREEN if identified else typer.colors.YELLOW
    status_label = (
        f"{result['manufacturer']} · {result['ecu_family']}"
        if identified
        else "Unknown ECU — no extractor matched this binary"
    )
    status_line = "  " + typer.style(status_label, fg=status_colour, bold=True)

    table = _format_table(result)

    # --- Confidence section ---
    conf = result["confidence"]
    conf_colour = _TIER_COLOURS.get(conf["tier"], typer.colors.WHITE)
    conf_header = typer.style(
        f"\n  ── Confidence " + "─" * 37,
        bold=True,
    )
    conf_tier_line = (
        "  "
        + typer.style("Tier   ", dim=True)
        + typer.style(conf["tier"].upper(), fg=conf_colour, bold=True)
    )

    conf_signals_lines = []
    for sig in conf["signals"]:
        colour = typer.colors.GREEN if sig["delta"] >= 0 else typer.colors.RED
        marker = (
            typer.style("+", fg=colour, bold=True)
            if sig["delta"] >= 0
            else typer.style("-", fg=colour, bold=True)
        )
        conf_signals_lines.append(
            f"  {typer.style('Signal ', dim=True)} {marker}  {sig['label']}"
        )

    conf_signals = "\n".join(conf_signals_lines)

    warnings_block = _format_confidence_warnings(conf["warnings"], indent="  ")
    warnings_section = f"\n{warnings_block}" if warnings_block else ""

    confidence_section = (
        f"{conf_header}\n{conf_tier_line}\n{conf_signals}{warnings_section}"
    )

    # --- VIN candidate section (only when a >= 0.6 candidate exists) ---
    vin = result["vin"]
    vin_section = ""
    if vin is not None:
        vin_header = typer.style("\n  ── VIN candidate " + "─" * 28, bold=True)
        vin_lines = [f"  {vin['candidate']}  (confidence {vin['confidence']:.2f})"]
        if vin["decoded"]:
            bits = []
            if vin["manufacturer"]:
                bits.append(vin["manufacturer"])
            if vin["country"]:
                bits.append(vin["country"])
            if vin["years"]:
                bits.append(str(vin["years"][0]))
            if bits:
                vin_lines.append("  " + ", ".join(bits) + " (decoded, unverified)")
        vin_lines.append(
            "  "
            + typer.style(
                f"check digit {'valid' if vin['checksum_valid'] else 'invalid'} (ISO 3779)",
                dim=True,
            )
        )
        vin_section = f"{vin_header}\n" + "\n".join(vin_lines) + "\n"

    full_output = (
        f"{header}\n{status_line}\n\n{table}\n{confidence_section}\n{vin_section}"
    )

    if output:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", full_output)
        _write_output(plain, output)
    else:
        typer.echo(full_output)
