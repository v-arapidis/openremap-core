"""
openremap diff-maps <stock.bin> <tuned.bin>

Find matching calibration maps between two ECU binaries by axis fingerprint
and diff them cell-by-cell.

Examples:
    openremap diff-maps stock.bin tuned.bin
    openremap diff-maps stock.bin tuned.bin --threshold 5.0
    openremap diff-maps stock.bin tuned.bin --json
    openremap diff-maps stock.bin tuned.bin --top 20
"""

from __future__ import annotations

import json
import orjson
from pathlib import Path

import typer

from openremap.api.composite.diff_maps import (
    _group_id,
    _read_axis_values,
    _read_cells,
    build_diff_json,
    diff_maps_binary,
)
from openremap.core.cli.commands.scan_maps import _parse_region
from openremap.core.cli.io import load_binary_file
from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EXTENSIONS = {".bin", ".ori", ".hex", ".s19", ".srec", ".mot"}


def _bo_label(byte_order: str) -> str:
    """``'little'`` → ``'LE'``, ``'big'`` → ``'BE'``."""
    return "LE" if byte_order == "little" else "BE"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _read_bin(path: Path, label: str) -> bytes:
    """Read + decode a binary file (raw, Intel HEX, or S-Record)."""
    if path.suffix.lower() not in VALID_EXTENSIONS:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, .hex, .s19, .srec, or .mot file.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    data, _fmt = load_binary_file(path, label)
    return data


def _load_recipe(path: Path) -> dict:
    """Load a .remap recipe file; exit 1 when unreadable or malformed."""
    try:
        data = orjson.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(
            typer.style(
                f"Error: cannot read recipe '{path.name}': {exc}",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    if not isinstance(data, dict) or "instructions" not in data:
        typer.echo(
            typer.style(
                f"Error: '{path.name}' is not a valid .remap recipe "
                f"(no 'instructions' field).",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    return data


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _dim_str(t: MapTable) -> str:
    """``16×16``"""
    return f"{t.cols}×{t.rows}"


def _pct_str(value: float) -> str:
    """Format a percentage value for display, handling inf."""
    if value == float("inf"):
        return "    +∞"
    if value == float("-inf"):
        return "    -∞"
    return f"{value:>+7.1f}%"


def _axis_preview(vals: tuple[int, ...], limit: int = 4) -> str:
    """Short human-readable axis preview: ``680, 685, 810, 925…+4``"""
    shown = ", ".join(str(v) for v in vals[:limit])
    if len(vals) > limit:
        shown += f"…+{len(vals) - limit}"
    return shown


def _print_map_grids(
    stock_data: bytes,
    tuned_data: bytes,
    m: dict,
    stock_table: MapTable,
) -> None:
    """Print before (stock) and diff (tuned − stock) grids for one map.

    Changed cells in the diff grid are highlighted yellow; zeros are dimmed.
    Axis values are shown as column/row headers.
    """
    cols = m["cols"]
    rows = m["rows"]
    bo = m["byte_order"]
    is_1d = rows == 1

    # Read axis values (promoted tables have none)
    x_vals: tuple[int, ...] = ()
    if stock_table is not None and stock_table.x_axis_offset is not None:
        x_vals = _read_axis_values(stock_data, stock_table.x_axis_offset, cols, bo)
    y_vals: tuple[int, ...] = ()
    if not is_1d and stock_table is not None and stock_table.y_axis_offset is not None:
        y_vals = _read_axis_values(stock_data, stock_table.y_axis_offset, rows, bo)

    # Read cells
    stock_cells = _read_cells(
        stock_data, m["offset_stock"], cols, rows, m["cell_width"], bo,
        m.get("stride"),
    )
    tuned_cells = _read_cells(
        tuned_data, m["offset_tuned"], cols, rows, m["cell_width"], bo,
        m.get("stride"),
    )
    diffs = [t - s for s, t in zip(stock_cells, tuned_cells)]

    cell_w = 6  # column width for values

    typer.echo("")
    typer.echo(
        typer.style(
            f"  Map 0x{m['offset_stock']:08X}  {cols}×{rows}  "
            f"u{m['cell_width'] * 8} {_bo_label(bo)}",
            bold=True,
        ),
    )

    # ── Stock grid ──────────────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  Stock:", fg=typer.colors.CYAN))

    # Column headers (X axis)
    if is_1d:
        pass  # 1D: no column header row
    else:
        line = "        "  # indent for Y axis label column
        for x in x_vals[:16]:
            line += f"{x:>{cell_w}}"
        if cols > 16:
            line += f"  … +{cols - 16}"
        typer.echo(typer.style(line, dim=True))

    # Rows
    for r in range(min(rows, 20)):
        if is_1d:
            # 1D: two-column format
            val = stock_cells[r]
            typer.echo(f"  {x_vals[r]:>6}  {val:>{cell_w}}")
        else:
            prefix = f"  {y_vals[r]:>6}" if r < len(y_vals) else "        "
            line = prefix
            for c in range(min(cols, 16)):
                val = stock_cells[r * cols + c]
                line += f"{val:>{cell_w}}"
            if cols > 16:
                line += f"  … +{cols - 16}"
            typer.echo(line)

    if rows > 20:
        typer.echo(typer.style(f"  … +{rows - 20} rows", dim=True))

    # ── Diff grid ───────────────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  Diff (tuned − stock):", fg=typer.colors.CYAN))

    # Column headers
    if not is_1d:
        line = "        "
        for x in x_vals[:16]:
            line += f"{x:>{cell_w}}"
        if cols > 16:
            line += f"  … +{cols - 16}"
        typer.echo(typer.style(line, dim=True))

    # Rows
    for r in range(min(rows, 20)):
        if is_1d:
            d = diffs[r]
            d_str = f"{d:>+{cell_w}}"
            if d != 0:
                d_str = typer.style(d_str, fg=typer.colors.YELLOW, bold=True)
            else:
                d_str = typer.style(d_str, dim=True)
            typer.echo(f"  {x_vals[r]:>6}  {d_str}")
        else:
            prefix = f"  {y_vals[r]:>6}" if r < len(y_vals) else "        "
            line = ""
            for c in range(min(cols, 16)):
                d = diffs[r * cols + c]
                d_str = f"{d:>+{cell_w}}"
                if d != 0:
                    d_str = typer.style(d_str, fg=typer.colors.YELLOW, bold=True)
                else:
                    d_str = typer.style(d_str, dim=True)
                line += d_str
            if cols > 16:
                line += f"  … +{cols - 16}"
            typer.echo(f"{prefix}{line}")

    if rows > 20:
        typer.echo(typer.style(f"  … +{rows - 20} rows", dim=True))


def _print_unmatched_map(data: bytes, t: MapTable, label: str) -> None:
    """Print a compact one-line summary of an unmatched map in verbose mode."""
    x_vals = _read_axis_values(data, t.x_axis_offset, t.cols, t.byte_order)
    y_vals: tuple[int, ...] = ()
    if t.y_axis_offset is not None and t.rows > 1:
        y_vals = _read_axis_values(data, t.y_axis_offset, t.rows, t.byte_order)

    x_preview = ", ".join(str(v) for v in x_vals[:6])
    if len(x_vals) > 6:
        x_preview += f"  … +{len(x_vals) - 6}"
    y_preview = ", ".join(str(v) for v in y_vals[:4]) if y_vals else "—"
    if len(y_vals) > 4:
        y_preview += f"  … +{len(y_vals) - 4}"

    typer.echo(
        f"    {label}  0x{t.offset:08X}  {t.cols}×{t.rows}  "
        f"u{t.cell_width * 8} {_bo_label(t.byte_order)}  "
        f"X=[{x_preview}]  Y=[{y_preview}]",
    )


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def _export_markdown(
    stock_name: str,
    tuned_name: str,
    matches: list[dict],
    unmatched_stock: list[MapTable],
    unmatched_tuned: list[MapTable],
    stock_data: bytes,
    tuned_data: bytes,
    path: Path,
    unidentified: list[dict] | None = None,
) -> None:
    """Write a single self-contained Markdown report.

    Renders natively on GitHub, in VS Code, or any Markdown viewer.
    Changed cells are **bold** in diff grids.
    """
    lines: list[str] = []

    lines.append(f"# Map Diff: `{stock_name}` vs `{tuned_name}`")
    lines.append("")
    lines.append(
        f"{len(matches):,} matched  •  "
        f"{len(unmatched_stock)} only-in-stock  •  "
        f"{len(unmatched_tuned)} only-in-tuned"
    )
    lines.append("")

    # ── Summary table ──────────────────────────────────────────────
    changed_maps = [m for m in matches if m["changed_cells"] > 0]
    if changed_maps:
        lines.append("## Changed Maps")
        lines.append("")
        lines.append(
            "| Offset | Dim | Max Δ | Avg Δ | Max % | Avg % | Changed |\n"
            "|--------|-----|-------|-------|-------|-------|---------|"
        )
        for m in changed_maps:
            lines.append(
                f"| `0x{m['offset_stock']:08X}` "
                f"| {m['cols']}×{m['rows']} "
                f"| {m['max_abs']:.1f} "
                f"| {m['avg_abs']:.1f} "
                f"| {_pct_str(m['max_pct']).strip()} "
                f"| {_pct_str(m['avg_pct']).strip()} "
                f"| {m['changed_cells']}/{m['total_cells']} |"
            )
        lines.append("")

    # ── Per-map grids ──────────────────────────────────────────────
    for m in changed_maps:
        cols = m["cols"]
        rows = m["rows"]
        bo = m["byte_order"]
        cw = m["cell_width"]
        is_1d = rows == 1
        le = bo == "little"

        st_ref = m.get("_stock_table")
        if st_ref is None:
            continue

        x_vals = _read_axis_values(stock_data, st_ref.x_axis_offset, cols, bo)
        y_vals: tuple[int, ...] = ()
        if not is_1d and st_ref.y_axis_offset is not None:
            y_vals = _read_axis_values(stock_data, st_ref.y_axis_offset, rows, bo)

        sc = _read_cells(
            stock_data, m["offset_stock"], cols, rows, cw, bo, m.get("stride"),
        )
        tc = _read_cells(
            tuned_data, m["offset_tuned"], cols, rows, cw, bo, m.get("stride"),
        )

        cell_w = "u8" if cw == 1 else "u16"

        lines.append(
            f"## `0x{m['offset_stock']:08X}` — {cols}×{rows} {cell_w} "
            f"{_bo_label(bo)}  "
            f"(max Δ {m['max_abs']:.1f}, "
            f"{_pct_str(m['max_pct']).strip()})"
        )
        if m.get("suspicious"):
            lines.append("")
            lines.append(
                "> ⚠ **Suspicious** — near-total cell change with weak "
                "correlation.  The grids may be misaligned (different maps "
                "sharing the same axes)."
            )
        lines.append("")

        if is_1d:
            lines.append("| X | Stock | Tuned |\n"
                         "|---|-------|-------|")
            for i in range(cols):
                t_val = str(tc[i])
                if tc[i] != sc[i]:
                    t_val = f"`{tc[i]}`"
                lines.append(
                    f"| {x_vals[i]} | {sc[i]} | {t_val} |"
                )
        else:
            header = "| | " + " | ".join(str(x) for x in x_vals) + " |"
            sep = "|---" * (cols + 1) + "|"

            lines.append("### Stock")
            lines.append("")
            lines.append(header)
            lines.append(sep)
            for r in range(rows):
                row = " | ".join(
                    [str(y_vals[r])]
                    + [str(sc[r * cols + c]) for c in range(cols)]
                )
                lines.append(f"| {row} |")
            lines.append("")

            lines.append("### Tuned  —  `code` = changed")
            lines.append("")
            lines.append(header)
            lines.append(sep)
            for r in range(rows):
                cells = []
                for c in range(cols):
                    val = tc[r * cols + c]
                    if val != sc[r * cols + c]:
                        cells.append(f"`{val}`")
                    else:
                        cells.append(str(val))
                row = " | ".join([str(y_vals[r])] + cells)
                lines.append(f"| {row} |")
        lines.append("")

    # ── Unmatched ──────────────────────────────────────────────────
    if unmatched_stock or unmatched_tuned:
        lines.append("## Unmatched")
        lines.append("")
        if unmatched_stock:
            lines.append(f"**Only in stock ({len(unmatched_stock)}):**  ")
            lines.append(
                ", ".join(
                    f"`0x{t.offset:08X}` ({t.cols}×{t.rows})"
                    for t in unmatched_stock
                )
            )
            lines.append("")
        if unmatched_tuned:
            lines.append(f"**Only in tuned ({len(unmatched_tuned)}):**  ")
            lines.append(
                ", ".join(
                    f"`0x{t.offset:08X}` ({t.cols}×{t.rows})"
                    for t in unmatched_tuned
                )
            )
            lines.append("")

    # ── Changed but not identified ─────────────────────────────────
    if unidentified:
        lines.append("## Changed but not identified")
        lines.append("")
        lines.append(
            f"{len(unidentified)} changed region(s) not recognised as "
            f"calibration tables:"
        )
        lines.append("")
        lines.append(
            ", ".join(
                f"`0x{r['offset']:08X}` ({r['size']} B)"
                for r in unidentified[:50]
            )
        )
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def diff_maps(
    stock: Path = typer.Argument(
        ...,
        help="Stock/original ECU binary (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    tuned: Path = typer.Argument(
        ...,
        help="Tuned/modified ECU binary (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    min_score: float = typer.Option(
        0.55,
        "--min-score",
        "-s",
        help="Minimum table score in [0, 1] (default: 0.55, lower than scan-maps to avoid missing changed maps).",
    ),
    threshold: float = typer.Option(
        0.0,
        "--threshold",
        "-t",
        help="Only show maps with max absolute cell change >= threshold.",
    ),
    top: int = typer.Option(
        50,
        "--top",
        "-n",
        help="Max matched maps to show (default: 50).",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        help="Group output: show only the top-3 changed maps per group.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show before/after cell grids for each changed map.",
    ),
    export: Path | None = typer.Option(
        None,
        "--export",
        help="Export the diff report as Markdown (diff.md) to a directory. Includes changed-map grids with changed values highlighted.",
        exists=False,
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="Restrict scanning to a byte range: '0xSTART-0xEND' (hex values, 0x optional). Overrides the calibration-region default.",
        metavar="RANGE",
    ),
    whole_file: bool = typer.Option(
        False,
        "--whole-file",
        help="Scan the whole file instead of only the detected calibration region (shows tables outside it).",
    ),
    max_series_tables: int = typer.Option(
        16,
        "--max-series-tables",
        help="Max consecutive shared-axis tables to probe (1=off, default: 16).",
    ),
    recipe: Path | None = typer.Option(
        None,
        "--recipe",
        help="Cross-reference a .remap recipe: mark which cells each recipe instruction covers and report changed cells NOT in the recipe (untracked changes).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    annotate: Path | None = typer.Option(
        None,
        "--annotate",
        help="With --recipe: write the recipe augmented with a schema 4.4 'maps' layer (map descriptors + instruction refs) to this path.",
        exists=False,
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
    ),
) -> None:
    """
    Diff calibration maps between a stock and tuned ECU binary.

    Scans both files for calibration tables, matches them by axis
    fingerprint (X and Y axis value tuples), and reports cell-by-cell
    changes for each matched pair.

    Maps whose axis breakpoints changed between stock and tuned will
    appear as unmatched rather than matched — this is intentional.
    Tuners rarely change breakpoints; when they do, the map structure
    itself has changed.

    Useful for auditing a tune: which maps were modified, how aggressive
    each change is, and whether anything looks suspicious.
    """
    # ── Read binaries + recipe ───────────────────────────────────────
    stock_data = _read_bin(stock, "Stock")
    tuned_data = _read_bin(tuned, "Tuned")
    region_slice = _parse_region(region)

    recipe_data: dict | None = None
    if recipe is not None:
        recipe_data = _load_recipe(recipe)

    # ── Run the core pipeline (API composite) ────────────────────────
    result = diff_maps_binary(
        stock_data,
        tuned_data,
        region=region_slice,
        min_score=min_score,
        max_series_tables=max_series_tables,
        layout_default=not whole_file,
        recipe_data=recipe_data,
    )

    matches = result["matches"]
    unmatched_stock = result["unmatched_stock"]
    unmatched_tuned = result["unmatched_tuned"]
    unidentified = result["unidentified"]
    stock_result = result["stock_result"]
    tuned_result = result["tuned_result"]
    recipe_summary = result["recipe_summary"]
    elapsed_scan = result["elapsed_scan"]
    stock_tables = stock_result["tables"]
    tuned_tables = tuned_result["tables"]

    # ── Recipe cross-reference (optional) ─────────────────────────────
    if annotate is not None and recipe_data is not None:
        from openremap.core.services.recipes.recipe_maps import attach_maps

        attach_maps(recipe_data, stock_data)
        annotate.parent.mkdir(parents=True, exist_ok=True)
        annotate.write_text(
            json.dumps(recipe_data, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        typer.echo(
            typer.style(
                f"\n  Annotated recipe written to {annotate} "
                f"({len(recipe_data['maps'])} map(s), schema 4.4)",
                fg=typer.colors.GREEN,
            ),
            err=True,
        )

    # Apply threshold
    def _sort_key(m: dict) -> float:
        val = m["max_abs"]
        if isinstance(val, float) and val == float("inf"):
            return float("inf")
        return float(val)

    above_threshold = [
        m for m in matches
        if (
            isinstance(m["max_abs"], float) and m["max_abs"] == float("inf")
        ) or m["max_abs"] >= threshold
    ]
    shown = above_threshold[:top]

    # ── JSON output ──────────────────────────────────────────────────
    if as_json:
        out = build_diff_json(
            str(stock),
            str(tuned),
            result,
            threshold=threshold,
            top=top,
            recipe_label=str(recipe) if recipe is not None else None,
        )
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        return

    # ── Human-readable output ────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Map-Level Diff", bold=True))
    typer.echo(
        typer.style(
            f"  {stock.name}  vs  {tuned.name}  •  "
            f"{len(matches):,} matched  •  "
            f"{len(unmatched_stock)} only-in-stock  •  "
            f"{len(unmatched_tuned)} only-in-tuned  •  "
            f"{len(unidentified)} unidentified  •  "
            f"scan {elapsed_scan:.1f}s",
            dim=True,
        ),
    )
    hidden_total = (
        stock_result.get("tables_hidden", 0)
        + tuned_result.get("tables_hidden", 0)
    )
    if hidden_total:
        typer.echo(
            typer.style(
                f"  ({hidden_total} table(s) outside the calibration region "
                f"hidden — use --whole-file to scan the whole file)",
                dim=True,
            ),
        )
    typer.echo("")

    # Heuristic: if very few maps matched, the files may be unrelated
    smaller_total = min(len(stock_tables), len(tuned_tables))
    match_pct = (len(matches) / smaller_total * 100) if smaller_total else 0

    if match_pct < 5:
        typer.echo(
            typer.style(
                f"  ⚠  Only {len(matches)} maps matched ({match_pct:.1f}% of "
                f"{smaller_total} tables in the smaller file).  "
                f"These files may not be from the same ECU, or one may not "
                f"be a tune of the other.",
                fg=typer.colors.YELLOW,
            ),
        )
        typer.echo("")

    if not matches:
        typer.echo(
            typer.style(
                "  No matching maps found between the two files.  "
                "The tuned file may be from a different ECU or the axis "
                "breakpoints were changed.",
                fg=typer.colors.YELLOW,
            ),
        )
        typer.echo("")
        return

    if recipe_summary:
        covered = recipe_summary["covered_changed_cells"]
        untracked = recipe_summary["untracked_changed_cells"]
        typer.echo(
            typer.style(
                f"  ◆ Recipe cross-reference ({recipe.name})\n"
                f"    {recipe_summary['instructions']} instruction(s) touch "
                f"{recipe_summary['maps_touched']} map(s) — "
                f"{covered} of the changed cells are covered.\n"
                f"    Untracked: {untracked} changed cell(s) not present "
                f"in the recipe.",
                fg=typer.colors.CYAN,
            ),
        )
        typer.echo("")

    # ── Group matched maps by axis fingerprint ─────────────────────────
    # Maps sharing identical X/Y axis breakpoints form a group (the same
    # RPM×Load axes often drive fuel, timing, and boost maps together).
    from collections import defaultdict as _defaultdict

    grouped: dict[tuple, list[dict]] = _defaultdict(list)
    for m in shown:
        grouped[m["_fp"]].append(m)
    # Order groups by their most-changed member (descending).
    group_items = sorted(
        grouped.items(),
        key=lambda kv: _sort_key(kv[1][0]),
        reverse=True,
    )

    # ── Matched table ────────────────────────────────────────────────
    hdr = typer.style(
        f"  {'Offset':>10}  {'Dim':>8}  {'Cells':>7}  "
        f"{'Max Δ':>8}  {'Avg Δ':>8}  {'Max %':>8}  {'Avg %':>8}  "
        f"{'Changed':>8}",
        bold=True,
    )
    typer.echo(hdr)
    typer.echo(typer.style("  " + "─" * 80, dim=True))

    def _row_text(m: dict) -> str:
        dim = f"{m['cols']}×{m['rows']}"
        cells = (
            f"{'u8' if m['cell_width'] == 1 else 'u16'} "
            f"{_bo_label(m['byte_order'])}"
        )
        changed = f"{m['changed_cells']}/{m['total_cells']}"

        # Colour the max_abs cell
        if isinstance(m["max_abs"], float) and m["max_abs"] == float("inf"):
            max_colour = typer.colors.RED
            max_display = "     ∞"
        else:
            if m["max_abs"] >= 20:
                max_colour = typer.colors.RED
            elif m["max_abs"] >= 5:
                max_colour = typer.colors.YELLOW
            else:
                max_colour = typer.colors.GREEN
            max_display = f"{m['max_abs']:8.2f}"

        marker = ""
        if m.get("recipe_cells_covered"):
            covered = m["recipe_cells_covered"]
            changed = m.get("changed_cells", 0)
            marker += typer.style(
                f"  ◆ recipe {covered}/{changed}",
                fg=typer.colors.CYAN,
            )
        if m.get("near_match"):
            marker += typer.style(
                "  ↺ axes changed (correlation near-match)",
                fg=typer.colors.YELLOW,
            )
        elif m.get("promoted"):
            marker += typer.style(
                "  ⚑ no-axis table (detected from changed bytes)",
                fg=typer.colors.YELLOW,
                dim=True,
            )
        elif m.get("suspicious"):
            marker += typer.style(
                "  ⚠ suspicious (grids don't line up — different map?)",
                fg=typer.colors.RED,
                bold=True,
            )
        elif m.get("realigned"):
            marker += typer.style("  ↻ realigned", fg=typer.colors.YELLOW, dim=True)
        corr = m.get("correlation")
        if m.get("suspicious") and corr is not None:
            marker += typer.style(
                f" · r={corr:.2f}", fg=typer.colors.RED, dim=True,
            )

        return (
            f"  0x{m['offset_stock']:08X}  {dim:>8}  {cells:>7}  "
            + typer.style(max_display, fg=max_colour)
            + f"  {m['avg_abs']:8.2f}  "
            + f"{_pct_str(m['max_pct'])}  {_pct_str(m['avg_pct'])}  "
            + f"{changed:>8}"
            + marker
        )

    for gi, (_fp, members) in enumerate(group_items):
        anchor = members[0]
        x_vals, y_vals = _fp
        if not x_vals and not y_vals:
            group_label = (
                f"Group {_group_id(gi)} — {len(members)} changed-block table(s) · "
                f"no axes (detected from changed bytes)"
            )
        else:
            group_label = (
                f"Group {_group_id(gi)} — {len(members)} map(s) · "
                f"{anchor['cols']}×{anchor['rows']} "
                f"{'u8' if anchor['cell_width'] == 1 else 'u16'} "
                f"{_bo_label(anchor['byte_order'])} · "
                f"X=[{_axis_preview(x_vals)}]"
                + (f"  Y=[{_axis_preview(y_vals)}]" if y_vals else "")
            )
        typer.echo(typer.style(f"  {group_label}", fg=typer.colors.CYAN, bold=True))

        visible = members[:3] if compact else members
        for m in visible:
            typer.echo(_row_text(m))
        if compact and len(members) > len(visible):
            typer.echo(
                typer.style(
                    f"      … and {len(members) - len(visible)} more in this group",
                    dim=True,
                )
            )
        typer.echo("")

    # ── Markdown export ────────────────────────────────────────────────
    if export is not None:
        export.mkdir(parents=True, exist_ok=True)

        _export_markdown(
            stock.name, tuned.name,
            matches, unmatched_stock, unmatched_tuned,
            stock_data, tuned_data,
            export / "diff.md",
            unidentified,
        )

        typer.echo(
            typer.style(
                "  Exported: diff.md",
                fg=typer.colors.GREEN,
            ),
        )
        typer.echo(f"  {export}/")
        typer.echo("")

    # ── Verbose: before/after cell grids ────────────────────────────
    if verbose:
        for m in shown:
            if "_stock_table" in m:
                _print_map_grids(
                    stock_data, tuned_data, m, m.pop("_stock_table"),
                )

    # Clean up internal refs from all match dicts
    for m in matches:
        m.pop("_stock_table", None)
        m.pop("_tuned_table", None)
        m.pop("_fp", None)

    if len(matches) > len(shown):
        remaining = len(matches) - len(shown)
        typer.echo(
            typer.style(
                f"  … and {remaining} more matched maps.  "
                f"Use --top {min(len(matches), top + remaining)} to see all, "
                f"or --threshold to filter.",
                dim=True,
            ),
        )
        typer.echo("")

    if threshold > 0 and len(above_threshold) < len(matches):
        hidden = len(matches) - len(above_threshold)
        typer.echo(
            typer.style(
                f"  {hidden} map(s) hidden by --threshold {threshold}.",
                dim=True,
            ),
        )
        typer.echo("")

    # ── Unmatched section ────────────────────────────────────────────
    if unmatched_stock or unmatched_tuned:
        typer.echo(
            typer.style(
                "  ── Unmatched ─────────────────────────────────────────────",
                bold=True,
            ),
        )
        typer.echo("")

        if unmatched_stock:
            label = typer.style(
                f"  Only in stock ({len(unmatched_stock)}):",
                fg=typer.colors.CYAN,
            )
            preview = ", ".join(
                f"0x{t.offset:08X} ({_dim_str(t)})"
                for t in unmatched_stock[:8]
            )
            if len(unmatched_stock) > 8:
                preview += f"  … +{len(unmatched_stock) - 8} more"
            typer.echo(f"{label}  {preview}")
            typer.echo("")

            if verbose:
                for t in unmatched_stock[:30]:
                    _print_unmatched_map(stock_data, t, "stock")
                if len(unmatched_stock) > 30:
                    typer.echo(
                        typer.style(
                            f"    … +{len(unmatched_stock) - 30} more",
                            dim=True,
                        ),
                    )
                typer.echo("")

        if unmatched_tuned:
            label = typer.style(
                f"  Only in tuned ({len(unmatched_tuned)}):",
                fg=typer.colors.CYAN,
            )
            preview = ", ".join(
                f"0x{t.offset:08X} ({_dim_str(t)})"
                for t in unmatched_tuned[:8]
            )
            if len(unmatched_tuned) > 8:
                preview += f"  … +{len(unmatched_tuned) - 8} more"
            typer.echo(f"{label}  {preview}")
            typer.echo("")

            if verbose:
                for t in unmatched_tuned[:30]:
                    _print_unmatched_map(tuned_data, t, "tuned")
                if len(unmatched_tuned) > 30:
                    typer.echo(
                        typer.style(
                            f"    … +{len(unmatched_tuned) - 30} more",
                            dim=True,
                        ),
                    )
                typer.echo("")

    # ── Changed but not identified ─────────────────────────────────
    if unidentified:
        typer.echo(
            typer.style(
                "  ── Changed but not identified ────────────────────────────",
                bold=True,
            ),
        )
        typer.echo("")
        label = typer.style(
            f"  {len(unidentified)} changed region(s) not recognised as "
            f"calibration tables:",
            fg=typer.colors.YELLOW,
        )
        preview = ", ".join(
            f"0x{r['offset']:08X} ({r['size']} B)"
            for r in unidentified[:8]
        )
        if len(unidentified) > 8:
            preview += f"  … +{len(unidentified) - 8} more"
        typer.echo(f"{label}  {preview}")
        typer.echo("")

    typer.echo("")
