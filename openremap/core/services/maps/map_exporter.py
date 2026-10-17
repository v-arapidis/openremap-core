"""
Calibration map CSV exporter — WinOLS-compatible grid format.

**Pure**: re-reads axis values and cell data from the original binary
(``MapTable`` stores byte offsets, not decoded values) and returns one CSV
*document per table*, keyed by the file name it should be saved under.  No
file I/O happens here — the caller (CLI/API) decides where files land, so this
module keeps the service-layer contract (``bytes``/``dict`` in, no paths, no
side effects).
"""

from __future__ import annotations

import csv
import io
import struct

from openremap._rust import read_cells as _rs_read_cells  # type: ignore[import-untyped]
from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_tables_csv(
    data: bytes,
    tables: list[MapTable],
) -> dict[str, str]:
    """Render each table as a CSV document, keyed by output file name.

    Parameters
    ----------
    data:
        The original ECU binary — axis and cell values are re-read from here.
    tables:
        Tables to export (already filtered by ``--min-score`` / ``--top``).

    Returns
    -------
    dict[str, str]
        ``{file name: CSV text}``, one entry per table, in input order.  The
        table offset is part of the name, so geometry can never collide.

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
    return {_filename(t): _render_csv(_read_table(data, t)) for t in tables}


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
    ``None`` means contiguous rows.  Delegates to the Rust ``read_cells``
    primitive (shared with ``diff_maps`` / ``map_classifier``).
    """
    return _rs_read_cells(
        data, offset, cols, rows, cell_width,
        "little" if le else "big", stride,
    )


def _render_csv(rows: list[list[str]]) -> str:
    """Serialise rows to CSV text (LF line endings, same quoting as ``csv``)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerows(rows)
    return buf.getvalue()
