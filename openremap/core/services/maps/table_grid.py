"""
Read one detected table as a grid of numbers.

The map scanner reports *where* tables are; the grid is what a human actually
recognises. This is the service the TUI previews with — the renderer lives in the
UI, the reading lives here, so nothing outside the service layer has to know about
cell widths, byte order or strided (compound) layouts.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

from openremap._rust import read_cells as _rs_read_cells  # type: ignore[import-untyped]

__all__ = ["grid_summary", "table_grid"]


def table_grid(data: bytes, table: Mapping[str, Any]) -> list[list[int]]:
    """Return the table's cells as ``rows`` lists of ``cols`` integers.

    Honours ``cell_width`` (1 or 2 bytes), ``byte_order`` and ``stride`` — the
    last one covers compound tables, where two maps share a Y axis and their rows
    interleave.
    """
    cols = int(table.get("cols") or 0)
    rows = int(table.get("rows") or 0)
    if cols <= 0 or rows <= 0:
        return []

    flat = _rs_read_cells(
        data,
        int(table.get("offset") or 0),
        cols,
        rows,
        int(table.get("cell_width") or 2),
        str(table.get("byte_order") or "little"),
        table.get("stride"),
    )
    return [list(flat[r * cols : (r + 1) * cols]) for r in range(rows)]


def grid_summary(grid: list[list[int]]) -> dict[str, Any]:
    """min / max / mean / distinct count — the numbers a preview line shows."""
    values = [cell for row in grid for cell in row]
    if not values:
        return {"count": 0, "min": 0, "max": 0, "mean": 0.0, "distinct": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
        "distinct": len(set(values)),
    }


def read_axis(data: bytes, offset: int, length: int, *, cell_width: int, byte_order: str) -> list[int] | None:
    """Read *length* axis breakpoints at *offset*, or ``None`` if they are not one.

    The scanner locates axes as strictly-increasing runs, so re-reading them here
    is a *check*: if the bytes at the recorded offset do not form a monotonic run
    of that length, the caller is told "no axis" instead of being handed numbers
    that would be drawn on the picture as if they were breakpoints.
    """
    if offset <= 0 or length <= 0:
        return None
    try:
        values = _rs_read_cells(
            data, int(offset), int(length), 1, int(cell_width), str(byte_order), None
        )
    except (ValueError, IndexError):
        return None
    values = [int(v) for v in values]
    if len(values) != length:
        return None
    rising = all(later > earlier for earlier, later in itertools.pairwise(values))
    falling = all(later < earlier for earlier, later in itertools.pairwise(values))
    if rising or falling:
        return values
    return None
