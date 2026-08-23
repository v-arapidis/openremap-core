"""
Calibration map CSV exporter — WinOLS-compatible grid format.

Re-reads axis values and cell data from the original binary (``MapTable``
stores byte offsets, not decoded values) and writes one CSV file per table.
"""

from __future__ import annotations

import csv
import struct
from pathlib import Path

from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_tables_csv(
    data: bytes,
    tables: list[MapTable],
    output_dir: Path,
) -> int:
    """Write each table as a CSV file in *output_dir*.

    Parameters
    ----------
    data:
        The original ECU binary — axis and cell values are re-read from here.
    tables:
        Tables to export (already filtered by ``--min-score`` / ``--top``).
    output_dir:
        Directory to write CSV files into.  Created if it doesn't exist.

    Returns
    -------
    int
        Number of CSV files written.

    Format — 2D tables (WinOLS grid)
    --------------------------------
    First row: empty cell followed by X axis values.
    Subsequent rows: Y axis value followed by data cells.

    .. code-block:: text

        ,500,1000,1500,2000,2500
        200,45.2,46.1,47.3,48.0,49.2
        400,44.8,45.9,47.0,47.8,49.0

    Format — 1D tables (``rows == 1``, no Y axis)
    ----------------------------------------------
    Two columns: ``AxisValue, DataValue``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for t in tables:
        path = output_dir / _filename(t)
        rows_data = _read_table(data, t)
        _write_csv(path, rows_data)
        written += 1

    return written


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filename(t: MapTable) -> str:
    """``0x000376F2_32x16_u16.csv``"""
    cw = "u8" if t.cell_width == 1 else "u16"
    return f"0x{t.offset:08X}_{t.cols}x{t.rows}_{cw}.csv"


def _read_table(
    data: bytes,
    t: MapTable,
) -> list[list[str]]:
    """Read axis and cell values from *data*, return CSV-ready rows.

    Axes are always u16 regardless of cell width.
    """
    le = t.byte_order == "little"

    # --- read X axis (always u16) ---
    x_fmt = f"{'<' if le else '>'}{t.cols}H"
    x_vals = list(struct.unpack_from(x_fmt, data, t.x_axis_offset))

    # --- read cells ---
    cells = _read_cells(
        data, t.offset, t.cols, t.rows, t.cell_width, le, t.stride,
    )

    # --- 1D: two-column tall format ---
    if t.rows == 1 or t.y_axis_offset is None:
        out: list[list[str]] = []
        for i in range(t.cols):
            out.append([str(x_vals[i]), str(cells[i])])
        return out

    # --- read Y axis (always u16) ---
    y_fmt = f"{'<' if le else '>'}{t.rows}H"
    y_vals = list(struct.unpack_from(y_fmt, data, t.y_axis_offset))

    # --- 2D: WinOLS grid ---
    # Header row: empty cell + X axis
    header = [""] + [str(x) for x in x_vals]
    rows_out = [header]
    for r in range(t.rows):
        row = [str(y_vals[r])]
        for c in range(t.cols):
            row.append(str(cells[r * t.cols + c]))
        rows_out.append(row)

    return rows_out


def _read_cells(
    data: bytes,
    offset: int,
    cols: int,
    rows: int,
    cell_width: int,
    le: bool,
    stride: int | None = None,
) -> list[int]:
    """Decode a rectangular block of cells.

    *stride* is the bytes-per-row of a compound (interleaved) table half;
    ``None`` means contiguous rows.
    """
    count = cols * rows
    row_bytes = cols * cell_width
    if stride is None or stride == row_bytes:
        if cell_width == 1:
            return list(data[offset : offset + count])
        fmt = f"{'<' if le else '>'}{count}H"
        return list(struct.unpack_from(fmt, data, offset))

    out: list[int] = []
    for r in range(rows):
        row_off = offset + r * stride
        if cell_width == 1:
            out.extend(data[row_off : row_off + cols])
        else:
            fmt = f"{'<' if le else '>'}{cols}H"
            out.extend(struct.unpack_from(fmt, data, row_off))
    return out


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write rows to a CSV file."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerows(rows)
