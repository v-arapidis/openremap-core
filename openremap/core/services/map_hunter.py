"""
Generic calibration map axis and table scanner.

Scans ECU binaries for monotonically increasing 16-bit axis sequences that
indicate genuine calibration map structures.  Used as a confidence signal:
if an extractor identifies a binary as a modern ECU but zero map axes are
found, the file may be encrypted, corrupted, or misidentified.

The scanner is intentionally conservative — it looks for *plausible* axes
rather than trying to parse any specific map format.

All heavy lifting runs on the compiled Rust backend via PyO3 (24–115×
faster than pure Python).  This module is a thin wrapper that converts
between Python data classes and the Rust FFI types.
"""

from __future__ import annotations

from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


class MapAxis(NamedTuple):
    """A single plausible calibration map axis found in the binary."""

    offset: int
    """Byte offset where the axis starts."""

    length: int
    """Number of 16-bit values in the axis."""

    byte_order: str
    """Either ``'little'`` or ``'big'``."""

    values: tuple[int, ...]
    """The decoded 16-bit values forming the axis."""


class MapTable(NamedTuple):
    """A plausible 2D calibration table located by axis pairing.

    A table is a rectangular block of ``cols * rows`` values that follows a
    pair of monotonically-increasing axes (X then Y) in the binary.  Layout
    assumed:

    ``[ X axis (cols * 2 B) | Y axis (rows * 2 B) | data (cols * rows * cell_width B) ]``

    This covers the single most common WinOLS / DAMOS table layout.  1D tables
    (one axis followed by a data vector) are reported as tables with
    ``rows == 1`` and ``y_axis_offset is None``.
    """

    offset: int
    """Byte offset where the data block starts (after the axes)."""

    cols: int
    """Number of columns — equals length of the X axis."""

    rows: int
    """Number of rows — equals length of the Y axis (1 for vector tables)."""

    cell_width: int
    """Bytes per cell (1 or 2)."""

    byte_order: str
    """Either ``'little'`` or ``'big'`` — matches the axes."""

    x_axis_offset: int
    """Byte offset of the X axis (cols values)."""

    y_axis_offset: int | None
    """Byte offset of the Y axis, or ``None`` for 1D / vector tables."""

    score: float
    """Heuristic confidence in ``[0.0, 1.0]`` — higher is better."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_SERIES_TABLES = 16


# ---------------------------------------------------------------------------
# Rust-accelerated dispatch
# ---------------------------------------------------------------------------

from openremap._rust import scan_map_axes as _rs_scan_map_axes   # type: ignore[import-untyped]
from openremap._rust import scan_map_tables as _rs_scan_map_tables  # type: ignore[import-untyped]

_MAP_BACKEND = "rust"


def map_hunter_backend() -> str:
    """Return which backend is active: ``"rust"``."""
    return _MAP_BACKEND


def scan_map_axes(
    data: bytes,
    region: slice | None = None,
    min_axis_length: int = 4,
    max_axis_length: int = 32,
    min_step: int = 1,
    max_step: int = 10_000,
) -> list[MapAxis]:
    """Scan *data* for plausible 16-bit calibration map axes.

    Parameters
    ----------
    data:
        Raw ECU binary content.
    region:
        Optional ``slice`` to restrict scanning to a sub-region of *data*.
    min_axis_length:
        Minimum number of consecutive strictly-increasing 16-bit values
        required to consider a run a plausible axis (default **4**).
    max_axis_length:
        Maximum axis length to consider (default **32**).
    min_step:
        Minimum allowed difference between consecutive axis values
        (default **1**).
    max_step:
        Maximum allowed difference between consecutive axis values
        (default **10 000**).

    Returns
    -------
    list[MapAxis]
        All plausible axes found, deduplicated across byte orders.
    """
    rs_start = -1
    rs_end = -1
    if region is not None:
        rs_start = region.start if region.start is not None else -1
        rs_end = region.stop if region.stop is not None else -1
    raw = _rs_scan_map_axes(data, rs_start, rs_end, min_axis_length,
                            max_axis_length, min_step, max_step)
    return [MapAxis(offset=o, length=l, byte_order=bo, values=tuple(v))
            for (o, l, bo, v) in raw]


def scan_map_tables(
    data: bytes,
    region: slice | None = None,
    axes: list[MapAxis] | None = None,
    min_score: float = 0.55,
    max_gap: int = 8,
    min_y_length: int = 3,
    min_axis_length: int = 4,
    cell_widths: tuple[int, ...] = (2, 1),
    max_results: int | None = 2000,
    max_series_tables: int = _MAX_SERIES_TABLES,
) -> list[MapTable]:
    """Scan *data* for plausible 2D calibration tables.

    Pairs axes returned by :func:`scan_map_axes` and promotes the bytes
    immediately following each pair into a `MapTable` candidate.

    Parameters
    ----------
    data:
        Raw ECU binary content.
    region:
        Optional ``slice`` to restrict scanning to a sub-region.
    axes:
        Pre-computed axis list.  If omitted, :func:`scan_map_axes` is
        called with its default parameters.
    min_score:
        Minimum heuristic score in ``[0, 1]`` for a candidate to be
        reported.  Defaults to ``0.55``.
    max_gap:
        Maximum bytes allowed between axis end and next axis / data
        start when pairing.  Defaults to ``8``.
    min_y_length:
        Minimum Y axis length considered during truncation.  Defaults
        to ``3``.
    min_axis_length:
        Minimum axis length passed to the greedy re-scan used for X
        truncation.  Defaults to ``4``.
    max_results:
        Optional hard cap on returned candidates after dedupe.  Pass
        ``None`` for unlimited.  Defaults to ``2000``.
    max_series_tables:
        Max consecutive shared-axis tables probed after each anchor
        (1 = off, default: 16).

    Returns
    -------
    list[MapTable]
        Candidate tables sorted by descending score.
    """
    rs_start = -1
    rs_end = -1
    if region is not None:
        rs_start = region.start if region.start is not None else -1
        rs_end = region.stop if region.stop is not None else -1

    rs_axes = None
    if axes is not None:
        rs_base = rs_start if rs_start >= 0 else 0
        rs_axes = [
            (a.offset - rs_base, a.length, a.byte_order, list(a.values))
            for a in axes
        ]

    raw = _rs_scan_map_tables(
        data, rs_start, rs_end, rs_axes, min_score, max_gap,
        min_y_length, min_axis_length, list(cell_widths),
        max_results if max_results is not None else 0,
        max_series_tables,
    )
    return [MapTable(offset=o, cols=c, rows=r, cell_width=cw,
                    byte_order=bo, x_axis_offset=xo, y_axis_offset=yo, score=s)
            for (o, c, r, cw, bo, xo, yo, s) in raw]


def count_map_axes(
    data: bytes,
    region: slice | None = None,
    min_axis_length: int = 4,
    max_axis_length: int = 32,
    min_step: int = 1,
    max_step: int = 10000,
) -> int:
    """Return the number of plausible calibration map axes in *data*.

    This is a thin convenience wrapper around :func:`scan_map_axes` — see
    that function's docstring for parameter details.
    """
    return len(
        scan_map_axes(
            data,
            region=region,
            min_axis_length=min_axis_length,
            max_axis_length=max_axis_length,
            min_step=min_step,
            max_step=max_step,
        )
    )
