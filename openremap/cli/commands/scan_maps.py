"""
openremap scan-maps <file|directory>

Scan an ECU binary (or a directory of binaries) for plausible calibration
map axes and tables without requiring manufacturer identification.

Examples:
    openremap scan-maps ecu.bin
    openremap scan-maps ecu.bin --top 10
    openremap scan-maps ecu.bin --min-score 0.7
    openremap scan-maps ecu.bin --json
    openremap scan-maps ./my_bins/
    openremap scan-maps ./my_bins/ --recursive
    openremap scan-maps ./my_bins/ --verbose
    openremap scan-maps ./my_bins/ --json
    openremap scan-maps ./my_bins/ --export ./csv_exports/
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import scan_map_axes, scan_map_tables

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EXTENSIONS = {".bin", ".ori", ".hex"}
def _bo_label(byte_order: str) -> str:
    """``'little'`` → ``'LE'``, ``'big'`` → ``'BE'``."""
    return "LE" if byte_order == "little" else "BE"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_bin(path: Path, label: str) -> bytes:
    """Read and validate a single ECU binary file."""
    if path.suffix.lower() not in VALID_EXTENSIONS:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, or .hex file.",
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


def _parse_region(region: str | None) -> slice | None:
    """Parse a --region string into a Python slice.

    Values are hexadecimal; the ``0x`` prefix is optional —
    ``0x10000-0x80000`` and ``10000-80000`` are equivalent.
    """
    if region is None:
        return None
    try:
        r = region.strip()
        parts = r.replace("..", "-").split("-")
        if len(parts) >= 2:
            start = int(parts[0].removeprefix("0x").removeprefix("0X"), 16)
            end = int(parts[-1].removeprefix("0x").removeprefix("0X"), 16)
            if end < start:
                typer.echo(
                    typer.style(
                        "Error: --region end is before start.", fg=typer.colors.RED,
                    ),
                    err=True,
                )
                raise typer.Exit(code=1)
            return slice(start, end)
        else:
            typer.echo(
                typer.style("Error: --region must be 'START-END' or '0xSTART-0xEND'.", fg=typer.colors.RED),
                err=True,
            )
            raise typer.Exit(code=1)
    except (ValueError, IndexError):
        typer.echo(
            typer.style("Error: invalid --region format. Use '0xSTART-0xEND' (hex, 0x optional).", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(code=1)


def _calibration_spans(data: bytes, tables) -> list[tuple[int, int]]:
    """Byte spans of the detected calibration region(s), or [] when none.

    The layout segmenter labels 64/16 KB sectors as ``calibration`` when
    they contain high-score (>= 0.85) tables — the map-density signal.
    The already-scanned tables are reused, so no second scan happens.
    An empty result means "no calibration signal" — the caller should
    fall back to a whole-file scan.
    """
    return [
        (r.start, r.end)
        for r in segment(data, tables=tables)
        if r.kind == "calibration"
    ]


def _apply_calibration_filter(data: bytes, result: dict) -> None:
    """Filter a scan result to the detected calibration region (in place).

    Tables outside the calibration region are dropped and counted in
    ``tables_hidden``; ``layout_filtered`` records whether the filter
    applied.  When the segmenter finds no calibration signal the result
    is left untouched (whole-file fallback).  Axes are deliberately NOT
    filtered — the axes-count health signal must keep its whole-file
    meaning.
    """
    spans = _calibration_spans(data, result["tables"])
    if not spans:
        result["layout_filtered"] = False
        result["tables_hidden"] = 0
        return

    def _inside(offset: int) -> bool:
        return any(s <= offset < e for s, e in spans)

    before = len(result["tables"])
    result["tables"] = [t for t in result["tables"] if _inside(t.offset)]
    result["layout_filtered"] = True
    result["tables_hidden"] = before - len(result["tables"])


def _scan_one(
    data: bytes,
    region_slice: slice | None,
    min_score: float,
    max_series_tables: int,
    layout_default: bool = False,
) -> dict:
    """Run axes + table scanning on a single binary.

    With ``layout_default`` (and no explicit ``--region``), the result is
    filtered to the detected calibration region — junk tables from code /
    erased sectors are hidden (counted in ``tables_hidden``), and a
    ``layout_filtered`` flag is recorded.  Without a calibration signal,
    the whole-file result is returned untouched.

    Returns a dict with keys: axes, tables, axes_count, tables_count,
    top_score, layout_filtered, tables_hidden.
    """
    axes = scan_map_axes(data, region=region_slice)
    tables = scan_map_tables(
        data, region=region_slice, axes=axes,
        min_score=min_score, max_series_tables=max_series_tables,
    )
    top_score = max((t.score for t in tables), default=0.0)
    result = {
        "axes": axes,
        "tables": tables,
        "axes_count": len(axes),
        "tables_count": len(tables),
        "top_score": top_score,
    }
    if layout_default and region_slice is None:
        _apply_calibration_filter(data, result)
        result["tables_count"] = len(result["tables"])
    return result


def _health_badge(axes_count: int) -> tuple[str, str]:
    """Return (badge_char, colour) for the axes-count health signal."""
    if axes_count >= 1000:
        return "✓", typer.colors.GREEN
    elif axes_count >= 100:
        return "⚠", typer.colors.YELLOW
    else:
        return "✗", typer.colors.RED


def _format_size(n: int) -> str:
    """Human-readable file size."""
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    elif n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


# ---------------------------------------------------------------------------
# Single-file output (human-readable, verbose)
# ---------------------------------------------------------------------------


def _label_cell(
    labels: dict[int, tuple[str, float]] | None, t,
) -> str:
    """Render a table's classifier label cell (empty when unavailable)."""
    if not labels:
        return ""
    label, conf = labels.get(t.offset, ("unknown", 0.0))
    colour = (
        typer.colors.GREEN if conf >= 0.7
        else typer.colors.YELLOW if conf >= 0.45
        else typer.colors.WHITE
    )
    return typer.style(f"{label} {conf:.2f}", fg=colour)


def _classify_for_file(data: bytes, tables) -> dict[int, tuple[str, float]]:
    """Classify tables, using the ECU family as fuel-type context."""
    from openremap.core.services.maps.map_classifier import (
        classify_tables,
        family_fuel_type,
    )

    family: str | None = None
    try:
        from openremap.core.services.identify.identifier import identify_ecu

        family = identify_ecu(data=data, filename="<scan>").get("ecu_family")
    except Exception:
        family = None
    return classify_tables(data, tables, fuel_type=family_fuel_type(family))


def _print_single_result(
    filename: str,
    data: bytes,
    axes,
    tables,
    top: int,
    show_series: bool,
    labels: dict[int, tuple[str, float]] | None = None,
    tables_hidden: int = 0,
) -> None:
    """Print the full human-readable table listing for one file."""
    typer.echo("")
    typer.echo(typer.style(f"  {filename}", fg=typer.colors.CYAN, bold=True))
    typer.echo("")

    # Health signal
    axes_count = len(axes)
    badge, colour = _health_badge(axes_count)
    if axes_count >= 1000:
        health_msg = "✓  Genuine calibration binary"
    elif axes_count >= 100:
        health_msg = "⚠  Few axes — possibly corrupted or trimmed"
    else:
        health_msg = "✗  Very few axes — likely encrypted, non-ECU, or empty"

    typer.echo(typer.style(f"  {health_msg}", fg=colour, bold=True))
    typer.echo(f"  {axes_count:,} axes  •  {len(tables):,} tables  •  {len(data):,} bytes")
    typer.echo("")

    if not tables:
        typer.echo(
            typer.style("  No tables found with the current min-score threshold.", dim=True)
        )
        typer.echo("")
        return

    # Table listing
    if show_series:
        from collections import defaultdict as _defaultdict
        groups: dict[tuple, list] = _defaultdict(list)
        for t in tables:
            key = (t.x_axis_offset, t.y_axis_offset, t.cols, t.rows, t.cell_width, t.byte_order)
            groups[key].append(t)
        grouped_items = sorted(groups.items(), key=lambda kv: -kv[1][0].score)

        hdr = typer.style(
            f"  {'Offset':>10}  {'Dim':>8}  {'Cells':>6}  {'Score':>7}  "
            + (f"{'Label':>22}  " if labels else "")
            + f"{'X Axis':>10}  {'Y Axis':>10}",
            bold=True,
        )
        typer.echo(hdr)
        typer.echo(typer.style("  " + "─" * 62, dim=True))

        shown = 0
        for _key, members in grouped_items:
            if shown >= top:
                break
            anchor = members[0]
            series = members[1:]
            for idx, t in enumerate([anchor] + series):
                if shown >= top:
                    break
                dim = f"{t.cols}×{t.rows}"
                cells = f"{'u8' if t.cell_width == 1 else 'u16'} {_bo_label(t.byte_order)}"
                score_colour = (
                    typer.colors.GREEN if t.score >= 0.85
                    else typer.colors.YELLOW if t.score >= 0.70
                    else typer.colors.WHITE
                )
                y_axis = f"0x{t.y_axis_offset:X}" if t.y_axis_offset is not None else "—"
                prefix = "└─" if idx > 0 else "  "
                label_str = _label_cell(labels, t)
                typer.echo(
                    f"{prefix} 0x{t.offset:08X}  {dim:>8}  {cells:>6}  "
                    + typer.style(f"{t.score:.3f}", fg=score_colour)
                    + (f"  {label_str:>20}" if labels else "")
                    + f"  0x{t.x_axis_offset:08X}  {y_axis}"
                )
                shown += 1
        total = shown
    else:
        hdr = typer.style(
            f"  {'Offset':>10}  {'Dim':>8}  {'Cells':>6}  {'Score':>7}  "
            + (f"{'Label':>22}  " if labels else "")
            + f"{'X Axis':>10}  {'Y Axis':>10}",
            bold=True,
        )
        typer.echo(hdr)
        typer.echo(typer.style("  " + "─" * 62, dim=True))

        for t in tables[:top]:
            dim = f"{t.cols}×{t.rows}"
            cells = f"{'u8' if t.cell_width == 1 else 'u16'} {_bo_label(t.byte_order)}"
            score_colour = (
                typer.colors.GREEN if t.score >= 0.85
                else typer.colors.YELLOW if t.score >= 0.70
                else typer.colors.WHITE
            )
            y_axis = f"0x{t.y_axis_offset:X}" if t.y_axis_offset is not None else "—"
            label_str = _label_cell(labels, t)

            typer.echo(
                f"  0x{t.offset:08X}  {dim:>8}  {cells:>6}  "
                + typer.style(f"{t.score:.3f}", fg=score_colour)
                + (f"  {label_str:>20}" if labels else "")
                + f"  0x{t.x_axis_offset:08X}  {y_axis}"
            )
        total = min(top, len(tables))

    typer.echo("")

    if len(tables) > total:
        typer.echo(
            typer.style(
                f"  … and {len(tables) - total} more.  Use --top {len(tables)} to see all, "
                "or --min-score 0.8 to filter.",
                dim=True,
            )
        )
        typer.echo("")

    if tables_hidden:
        typer.echo(
            typer.style(
                f"  {tables_hidden} table(s) outside the calibration region hidden — "
                "use --whole-file to scan the whole file.",
                dim=True,
            )
        )
        typer.echo("")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _build_json_result(
    filepath: Path,
    data: bytes,
    result: dict,
    top: int,
    labels: dict[int, tuple[str, float]] | None = None,
) -> dict:
    """Build a JSON-serialisable dict for one file."""
    axes = result["axes"]
    tables = result["tables"]
    return {
        "file": str(filepath),
        "file_size": len(data),
        "axes_count": result["axes_count"],
        "tables_count": result["tables_count"],
        "top_score": round(result["top_score"], 4),
        "layout_filtered": bool(result.get("layout_filtered")),
        "tables_hidden": result.get("tables_hidden", 0),
        "axes": [
            {"offset": a.offset, "length": a.length, "byte_order": a.byte_order,
             "values": list(a.values[:16])}
            for a in axes[:200]
        ],
        "tables": [
            {"offset": t.offset, "cols": t.cols, "rows": t.rows,
             "cell_width": t.cell_width, "byte_order": t.byte_order,
             "x_axis_offset": t.x_axis_offset, "y_axis_offset": t.y_axis_offset,
             "stride": t.stride, "score": t.score,
             "label": (labels or {}).get(t.offset, ("unknown", 0.0))[0],
             "label_confidence": (labels or {}).get(t.offset, ("unknown", 0.0))[1]}
            for t in tables[:top]
        ],
    }


# ---------------------------------------------------------------------------
# CSV export helpers
# ---------------------------------------------------------------------------


def _export_for_file(
    data: bytes,
    tables,
    top: int,
    export_dir: Path,
    file_stem: str,
) -> int:
    """Export tables for one file into a subdirectory named after the file stem."""
    from openremap.core.services.maps.map_exporter import export_tables_csv

    sub = export_dir / f"{file_stem}_maps"
    return export_tables_csv(data, tables[:top], sub)


# ---------------------------------------------------------------------------
# Batch file collection
# ---------------------------------------------------------------------------


def _collect_candidates(directory: Path, recursive: bool) -> list[Path]:
    """Collect .bin/.ori files from *directory*, optionally recursive."""
    if recursive:
        return sorted(
            f for f in directory.rglob("*")
            if f.is_file() and f.suffix.lower() in VALID_EXTENSIONS
        )
    return sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in VALID_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def scan_maps(
    path: Path = typer.Argument(
        ...,
        help="ECU binary file (.bin/.ori/.hex) or a directory of binaries to scan.",
        exists=True, file_okay=True, dir_okay=True, readable=True, resolve_path=True,
    ),
    top: int = typer.Option(20, "--top", "-n", help="Number of top-scoring tables to show per file (default: 20)."),
    min_score: float = typer.Option(0.85, "--min-score", "-s", help="Minimum table score in [0, 1] (default: 0.85)."),
    region: str | None = typer.Option(
        None, "--region", "-r",
        help="Restrict scanning to a byte range: '0xSTART-0xEND' or 'START-END' (hex values, 0x optional — e.g. '0x10000-0x80000'). Overrides the calibration-region default.",
        metavar="RANGE",
    ),
    whole_file: bool = typer.Option(
        False, "--whole-file",
        help="Scan the whole file (default: only the detected calibration region — use this to see tables outside it).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
    max_series_tables: int = typer.Option(
        16, "--max-series-tables",
        help="Max consecutive shared-axis tables to probe after each anchor (1 = off, default: 16).",
    ),
    show_series: bool = typer.Option(
        False, "--show-series",
        help="Group tables that share the same X/Y axes with indented continuation rows.",
    ),
    export: Path | None = typer.Option(
        None, "--export",
        help="Export found tables as CSV files. With a directory: one sub-folder per file.",
        exists=False, file_okay=False, dir_okay=True, writable=True, resolve_path=True,
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-R",
        help="Recurse into sub-directories when scanning a directory.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show the full per-file table listing in batch mode (default: one-line summary per file).",
    ),
    classify: bool = typer.Option(
        False, "--classify",
        help="Annotate tables with probabilistic content labels (fuel, timing, boost, torque, duration) from axis shapes and cell trends. No manufacturer catalog needed.",
    ),
) -> None:
    """
    Scan a binary (or a directory of binaries) for plausible calibration
    map axes and 2D tables.

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
      - Health-checking a whole folder of binaries at once.
        \b
        openremap scan-maps ./my_bins/

    The default --min-score of 0.85 filters out most coincidental patterns
    (code sections, pointer tables, lookup arrays).  Lower it to 0.55 for
    exhaustive scanning of unsupported ECUs; raise to 0.85 for high-confidence
    calibration maps only.
    """
    # ── Single file ────────────────────────────────────────────────────
    if path.is_file():
        data = _read_bin(path, "Binary")
        region_slice = _parse_region(region)

        result = _scan_one(
            data, region_slice, min_score, max_series_tables,
            layout_default=not whole_file,
        )
        axes = result["axes"]
        tables = result["tables"]
        labels = _classify_for_file(data, tables) if classify else None

        if as_json:
            out = _build_json_result(path, data, result, top, labels)
            typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            _print_single_result(
                path.name, data, axes, tables, top, show_series, labels,
                result.get("tables_hidden", 0),
            )

        # CSV export
        if export is not None:
            from openremap.core.services.maps.map_exporter import export_tables_csv

            to_export = tables[:top]
            n = export_tables_csv(data, to_export, export)
            typer.echo(
                typer.style(
                    f"  Exported {n} CSV file{'s' if n != 1 else ''} to {export}/",
                    fg=typer.colors.GREEN,
                )
            )
            typer.echo("")
        return

    # ── Directory — batch mode ─────────────────────────────────────────
    directory = path
    candidates = _collect_candidates(directory, recursive)

    if not candidates:
        typer.echo(
            typer.style(
                f"\n  No .bin/.ori/.hex files found in {directory}\n",
                fg=typer.colors.YELLOW,
            )
        )
        return

    total = len(candidates)
    region_slice = _parse_region(region)

    # Accumulators
    json_results: list[dict] = []
    json_errors: list[dict] = []
    health_counts: dict[str, int] = {"genuine": 0, "few": 0, "sparse": 0}
    exported_total = 0

    # Header (human output only — stdout must stay pure JSON in --json mode)
    if not as_json:
        typer.echo("")
        typer.echo(
            typer.style("  OpenRemap — Batch Map Scanner", bold=True)
        )
        rec_label = "  recursive" if recursive else ""
        typer.echo(
            typer.style(
                f"  {total} file(s){rec_label}  •  min-score {min_score}  •  {directory}",
                dim=True,
            )
        )
        typer.echo("")

    idx_width = len(str(total))
    t0_all = time.perf_counter()

    for idx, filepath in enumerate(candidates, start=1):
        label_idx = typer.style(f"[{idx:>{idx_width}}/{total}]", dim=True)
        display_name = (
            str(filepath.relative_to(directory)) if recursive else filepath.name
        )

        # Read
        try:
            data = filepath.read_bytes()
        except OSError as exc:
            if as_json:
                json_errors.append({"file": str(filepath), "error": f"READ ERR: {exc}"})
            else:
                typer.echo(
                    f"{label_idx}"
                    + typer.style("  READ ERR   ", fg=typer.colors.RED)
                    + f"{display_name}  ({exc})"
                )
            continue

        if len(data) == 0:
            if as_json:
                json_errors.append({"file": str(filepath), "error": "EMPTY file"})
            else:
                typer.echo(
                    f"{label_idx}"
                    + typer.style("  EMPTY      ", fg=typer.colors.RED)
                    + f"{display_name}"
                )
            continue

        # Scan
        t0 = time.perf_counter()
        result = _scan_one(
            data, region_slice, min_score, max_series_tables,
            layout_default=not whole_file,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Health classification
        ac = result["axes_count"]
        if ac >= 1000:
            health_counts["genuine"] += 1
        elif ac >= 100:
            health_counts["few"] += 1
        else:
            health_counts["sparse"] += 1

        badge, colour = _health_badge(ac)
        top_score_str = f"{result['top_score']:.3f}" if result["tables_count"] > 0 else "—"
        hidden_note = (
            f" · {result['tables_hidden']} hidden"
            if result.get("layout_filtered") and result["tables_hidden"]
            else ""
        )

        # One-line summary (human output only)
        if not as_json:
            timing = typer.style(f"  {elapsed_ms:6.1f} ms", dim=True)
            badge_styled = typer.style(badge, fg=colour, bold=True)
            typer.echo(
                f"{label_idx}  {badge_styled}  {display_name}  "
                f"{ac:,} axes  •  {result['tables_count']:,} tables"
                f"{hidden_note}  •  "
                f"top {top_score_str}  •  {_format_size(len(data))}"
                + timing
            )

            # Verbose: full listing
            if verbose:
                _print_single_result(
                    display_name, data, result["axes"], result["tables"],
                    top, show_series,
                    _classify_for_file(data, result["tables"]) if classify else None,
                    result.get("tables_hidden", 0),
                )

        # JSON accumulation
        if as_json:
            json_results.append(
                _build_json_result(
                    filepath, data, result, top,
                    _classify_for_file(data, result["tables"]) if classify else None,
                )
            )

        # CSV export
        if export is not None:
            stem = filepath.stem
            n = _export_for_file(data, result["tables"], top, export, stem)
            exported_total += n

    elapsed_total = time.perf_counter() - t0_all

    # ── JSON output ────────────────────────────────────────────────────
    if as_json:
        payload: dict = {
            "directory": str(directory),
            "files_scanned": total,
            "health": health_counts,
            "results": json_results,
        }
        if json_errors:
            payload["errors"] = json_errors
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # ── Summary ────────────────────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  ── Summary " + "─" * 40, bold=True))
    typer.echo(
        f"  {typer.style('✓  Genuine (1,000+ axes)', fg=typer.colors.GREEN)}     {health_counts['genuine']:>5}"
    )
    typer.echo(
        f"  {typer.style('⚠  Few axes (100–999)', fg=typer.colors.YELLOW)}       {health_counts['few']:>5}"
    )
    typer.echo(
        f"  {typer.style('✗  Sparse (<100 axes)', fg=typer.colors.RED)}         {health_counts['sparse']:>5}"
    )
    typer.echo(
        typer.style(f"\n  Total: {total}  •  {elapsed_total:.2f}s", dim=True)
    )

    # ── Export summary ─────────────────────────────────────────────────
    if export is not None and exported_total > 0:
        typer.echo(
            typer.style(
                f"\n  Exported {exported_total} CSV file{'s' if exported_total != 1 else ''} "
                f"to {export}/  (one sub-folder per file)",
                fg=typer.colors.GREEN,
            )
        )

    typer.echo("")
