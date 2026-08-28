"""
openremap analyze <FILE> [--json] [-o OUT] [--fast] [--no-maps]

One command that describes a whole ECU binary: container + hardware,
identity + confidence, VIN, flash layout, maps, checksums, and the health
verdict.  `identify` answers "what ECU is this?"; `analyze` answers
"tell me everything about this dump".

Examples:
    openremap analyze stock.bin
    openremap analyze stock.bin --json
    openremap analyze stock.bin --fast          # skip maps + checksums + health
    openremap analyze stock.bin --no-maps       # skip the map scan only
"""

import json as stdjson
from pathlib import Path
from typing import Optional

import typer

from openremap.core.arch import arch_for_family, decoder_label
from openremap.core.cli.io import CONTAINER_NAMES, load_binary_file
from openremap.core.services.analyze import AnalyzeReport, analyze_binary

_IDENTITY_LABELS = [
    ("manufacturer", "Manufacturer"),
    ("ecu_family", "ECU Family"),
    ("ecu_variant", "ECU Variant"),
    ("software_version", "Software Version"),
    ("hardware_number", "Hardware Number"),
    ("calibration_id", "Calibration ID"),
    ("match_key", "Match Key"),
]

_TIER_COLOURS = {
    "High": typer.colors.GREEN,
    "Medium": typer.colors.YELLOW,
    "Low": typer.colors.MAGENTA,
    "Suspicious": typer.colors.RED,
    "Unknown": typer.colors.CYAN,
}


def _section(title: str) -> str:
    return typer.style(f"\n  ── {title} " + "─" * max(1, 38 - len(title)), bold=True)


def _render(report: AnalyzeReport) -> str:
    """Human-readable sectioned report."""
    lines: list[str] = []

    # Header / container
    lines.append(f"\n  {report.container}  •  {report.file_size:,} bytes  •  {report.sha256[:16]}…")

    # Identity
    lines.append(_section("Identity"))
    status = (
        f"{report.identity['manufacturer']} · {report.identity['ecu_family']}"
        if report.identity.get("ecu_family")
        else "Unknown ECU — no extractor matched this binary"
    )
    lines.append("  " + typer.style(status, fg=typer.colors.GREEN if report.identity.get("ecu_family") else typer.colors.YELLOW, bold=True))
    for key, label in _IDENTITY_LABELS:
        value = report.identity.get(key)
        display = "unknown" if value is None else str(value)
        lines.append(f"  {label:<17}  {display}")
    lines.append(f"  {'Byte Order':<17}  {report.endian}-endian")
    lines.append(f"  {'Cell Size':<17}  {report.cell_bytes * 8}-bit")

    # Confidence
    tier = report.confidence.tier
    colour = _TIER_COLOURS.get(tier, typer.colors.WHITE)
    summary = report.confidence.rationale_summary(max_signals=3)
    lines.append(_section("Confidence"))
    lines.append("  " + typer.style(tier.upper(), fg=colour, bold=True) + typer.style(f"  {summary}", dim=True))
    for w in report.confidence.warnings:
        lines.append("  " + typer.style(f"⚠  {w}", fg=typer.colors.RED, bold=True))

    # Coherence — one consistency line (identity / checksums / arch must
    # agree, or explain why).  Green when everything agrees, red on a hard
    # conflict, neutral otherwise.
    if report.coherence is not None:
        coh = report.coherence
        marks = {
            "agree": "✓",
            "stale": "⚠",
            "gap": "–",
            "conflict": "✗",
            "n/a": "?",
        }
        bits = "  ".join(
            f"{c.name.removeprefix('identity_')} {marks.get(c.status, '?')}"
            for c in coh.checks
        )
        colour = {
            "agree": typer.colors.GREEN,
            "stale": typer.colors.YELLOW,
            "gap": typer.colors.BRIGHT_BLACK,
            "conflict": typer.colors.RED,
            "n/a": typer.colors.WHITE,
        }.get(coh.status, typer.colors.WHITE)
        lines.append(
            "  "
            + typer.style(
                f"Coherence: {bits}", fg=colour, bold=coh.status == "conflict"
            )
        )
        if coh.status == "conflict":
            for c in coh.checks:
                if c.status == "conflict":
                    lines.append(
                        "  "
                        + typer.style(
                            f"  ✗ {c.name}: {c.detail}",
                            fg=typer.colors.RED,
                            bold=True,
                        )
                    )

    # VIN
    if report.vin is not None and report.vin.decoded:
        bits = [b for b in (report.vin.manufacturer, report.vin.country, str(report.vin.years[0]) if report.vin.years else None) if b]
        lines.append(_section("VIN candidate"))
        lines.append(
            f"  {report.vin.vin}  (confidence {report.vin_confidence:.2f})"
            + (typer.style(f"  — {', '.join(bits)} (decoded, unverified)", dim=True) if bits else "")
        )

    # Layout
    if report.regions:
        lines.append(_section("Flash layout"))
        for r in report.regions:
            lines.append(
                f"  {r.kind:<12}  0x{r.start:06X}-0x{r.end:06X}  {r.size:>10,} B"
                f"{f'  {r.tables} tables' if r.tables else ''}"
            )
        if report.ident_blocks:
            blocks = [f"0x{b.start:06X}" for b in report.ident_blocks]
            shown = ", ".join(blocks[:8])
            more = f", … {len(blocks) - 8} more" if len(blocks) > 8 else ""
            lines.append(f"  ident blocks: {shown}{more}")

    # Maps
    if not report.fast:
        lines.append(_section("Maps"))
        if report.axis_count == 0 and not report.tables:
            lines.append("  no maps scanned")
        else:
            lines.append(
                f"  {report.axis_count:,} axis(es)  •  {len(report.tables):,} table(s)"
            )
            if report.xrefs is not None:
                xr = report.xrefs
                if xr.status == "ok":
                    label = decoder_label(xr.arch) or xr.arch
                    cascade_detected = (
                        arch_for_family(
                            report.identity.get("manufacturer"),
                            report.identity.get("ecu_family"),
                        )
                        is None
                    )
                    src = (
                        typer.style("  · cascade-detected", dim=True)
                        if cascade_detected
                        else ""
                    )
                    lines.append(
                        f"  code refs: {len(xr.referenced):,} reference(s) from "
                        f"{xr.insn_count:,} instructions [{label}] "
                        f"(base 0x{xr.base_address:X}, "
                        f"{xr.code_bytes_scanned:,} B code){src}"
                    )
                else:
                    lines.append(
                        typer.style(
                            f"  code refs: skipped ({xr.skip_reason})", dim=True
                        )
                    )
            from openremap.core.services.maps.xrefs import xref_evidence

            for t in sorted(report.tables, key=lambda t: t.score, reverse=True)[:5]:
                ev = xref_evidence(t, report.xrefs) if report.xrefs else {}
                marker = typer.style("  ⟶code", fg=typer.colors.CYAN) if ev.get("referenced_by_code") else ""
                lines.append(
                    f"    0x{t.offset:06X}  {t.cols}x{t.rows}  "
                    f"{t.cell_width}-byte cells  score {t.score:.2f}{marker}"
                )

    # Checksums
    if report.checksums is not None:
        lines.append(_section("Checksums"))
        ck = report.checksums
        if ck["me7"]:
            lines.append(f"  ME7: {ck['me7']['status']} ({ck['me7']['scheme']})")
        if ck["denso"]:
            lines.append(
                f"  Denso: {ck['denso']['ok']}/{ck['denso']['total']} entries ok "
                f"@ 0x{ck['denso']['table_offset']:06X}"
            )
        if ck["schemes"]:
            for s in ck["schemes"][:3]:
                lines.append(
                    f"  {s['algo']} ({s['region']})  {s['pages']} pages  rate {s['rate']}"
                )
        elif not ck["me7"] and not ck["denso"]:
            lines.append("  no known checksum scheme detected")

    # Health
    if report.health is not None:
        lines.append(_section("Health"))
        for c in report.health.checks:
            mark = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "–"}.get(c.status, "?")
            colour = {
                "ok": typer.colors.GREEN,
                "warn": typer.colors.YELLOW,
                "fail": typer.colors.RED,
                "skip": typer.colors.BRIGHT_BLACK,
            }.get(c.status, typer.colors.WHITE)
            lines.append(f"  {mark} " + typer.style(f"{c.name:<14}", fg=colour, bold=c.status == "fail") + f"  {c.message}")
        lines.append("")

    if report.fast:
        lines.append("")
        lines.append(
            typer.style("  ⚠  fast mode — maps, checksums, and health skipped (run without --fast)", fg=typer.colors.YELLOW)
        )

    return "\n".join(lines)


def analyze(
    file: Path = typer.Argument(
        ...,
        help="ECU binary to analyse (.bin/.ori/.hex/.s19/.srec/.mot).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output the full report as JSON.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save the report to a file instead of printing to stdout.",
        writable=True,
        resolve_path=True,
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Skip maps, checksums, and the health verdict (~1-2 s).",
    ),
    no_maps: bool = typer.Option(
        False,
        "--no-maps",
        help="Skip the map scan only (keeps checksums + health).",
    ),
) -> None:
    """Describe a whole ECU binary: identity, VIN, layout, maps, checksums, health."""
    data, fmt_code = load_binary_file(file, "Binary")
    container = CONTAINER_NAMES.get(fmt_code, fmt_code)

    try:
        report = analyze_binary(
            data,
            file.name,
            fast=fast,
            skip_maps=no_maps,
            container=container,
        )
    except Exception as exc:
        typer.echo(
            typer.style(f"Analysis failed: {exc}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=1)

    content = stdjson.dumps(report.to_dict(), indent=2) if as_json else _render(report)
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
