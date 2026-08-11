"""Tests for openremap.core.services.map_hunter."""

from __future__ import annotations

import struct

import pytest

from openremap.core.services.map_hunter import (
    MapAxis,
    MapTable,
    scan_map_axes,
    scan_map_tables,
)


def _pack_u16(values: list[int], byte_order: str = "little") -> bytes:
    fmt = ("<" if byte_order == "little" else ">") + "H" * len(values)
    return struct.pack(fmt, *values)


def _make_2d_table(
    x_axis: list[int],
    y_axis: list[int],
    cells: list[int],
    *,
    byte_order: str = "little",
    prefix: bytes = b"",
    suffix: bytes = b"",
) -> bytes:
    """Lay out [prefix | X axis | Y axis | cell rows | suffix]."""
    assert len(cells) == len(x_axis) * len(y_axis)
    return (
        prefix
        + _pack_u16(x_axis, byte_order)
        + _pack_u16(y_axis, byte_order)
        + _pack_u16(cells, byte_order)
        + suffix
    )


# ---------------------------------------------------------------------------
# scan_map_axes — keep the existing behaviour locked in.
# ---------------------------------------------------------------------------


class TestScanMapAxes:
    def test_finds_simple_monotonic_axis(self) -> None:
        data = b"\x00" * 16 + _pack_u16([100, 200, 300, 400, 500, 600]) + b"\x00" * 16
        axes = scan_map_axes(data)
        assert any(ax.length >= 6 and ax.byte_order == "little" for ax in axes)

    def test_ignores_runs_shorter_than_min(self) -> None:
        # Only 3 increasing values — below default min_axis_length=4.
        data = b"\x00" * 16 + _pack_u16([10, 20, 30]) + b"\xff" * 16
        axes = scan_map_axes(data)
        assert axes == []

    def test_respects_max_step(self) -> None:
        # 1 → 50000 is increasing but the step exceeds max_step=10000,
        # so the scanner must not include element 0 in any run.
        data = _pack_u16([1, 50000, 50001, 50002, 50003])
        axes = scan_map_axes(data)
        assert all(1 not in ax.values for ax in axes), (
            "max_step violation should split runs, not be absorbed"
        )


# ---------------------------------------------------------------------------
# scan_map_tables — the new axis-pair → table heuristic.
# ---------------------------------------------------------------------------


class TestScanMapTables:
    def test_detects_2d_table_with_smooth_gradient(self) -> None:
        # 6×4 table whose rows are smooth ramps — the canonical
        # calibration-table shape.
        x = [800, 1200, 1600, 2000, 2400, 2800]  # RPM
        y = [10, 20, 30, 40]  # load
        # Smooth bilinear-ish surface.
        cells = []
        for j, _ly in enumerate(y):
            for i, _lx in enumerate(x):
                cells.append(500 + i * 50 + j * 100)

        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 32, suffix=b"\x00" * 32)

        tables = scan_map_tables(data)
        assert tables, "expected at least one detected table"
        t = tables[0]
        assert t.cols == 6
        assert t.rows == 4
        assert t.cell_width == 2
        assert t.byte_order == "little"
        assert t.x_axis_offset == 32
        assert t.y_axis_offset == 32 + 6 * 2
        assert t.offset == 32 + 6 * 2 + 4 * 2
        assert 0.0 <= t.score <= 1.0
        assert t.score >= 0.55

    def test_rejects_flooded_zero_block(self) -> None:
        # Valid axes followed by a sea of zeros — must NOT promote to a table.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        cells = [0] * (len(x) * len(y))
        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 32, suffix=b"\x00" * 32)
        tables = scan_map_tables(data)
        assert (
            all(t.cols * t.rows == 0 or t.score < 0.55 for t in tables) or tables == []
        )

    def test_detects_1d_vector_table(self) -> None:
        # Single axis followed directly by a smooth value vector.
        axis = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000]
        values = [100, 110, 130, 160, 200, 250, 310, 380]
        data = b"\x00" * 32 + _pack_u16(axis) + _pack_u16(values) + b"\xff" * 32
        tables = scan_map_tables(data)
        vec_tables = [t for t in tables if t.rows == 1]
        assert vec_tables, "expected at least one 1D / vector table"
        t = vec_tables[0]
        assert t.cols == len(axis)
        assert t.y_axis_offset is None
        assert t.x_axis_offset == 32
        assert t.offset == 32 + len(axis) * 2

    def test_results_sorted_by_score_descending(self) -> None:
        # Two distinct tables in the same buffer.
        x1 = [100, 200, 300, 400, 500, 600]
        y1 = [10, 20, 30, 40]
        cells1 = [500 + i * 25 + j * 50 for j in range(4) for i in range(6)]
        block1 = _make_2d_table(x1, y1, cells1)

        x2 = [50, 100, 150, 200, 250, 300]
        y2 = [5, 10, 15, 20]
        cells2 = [200 + i * 10 + j * 20 for j in range(4) for i in range(6)]
        block2 = _make_2d_table(x2, y2, cells2)

        data = b"\x00" * 16 + block1 + b"\x00" * 32 + block2 + b"\x00" * 16
        tables = scan_map_tables(data)
        # At least two distinct table starts.
        offsets = {t.offset for t in tables}
        assert len(offsets) >= 2
        scores = [t.score for t in tables]
        assert scores == sorted(scores, reverse=True)

    def test_overlapping_candidates_deduplicated(self) -> None:
        # Build one valid table; ensure overlapping rectangles aren't both kept.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        cells = [500 + i * 25 + j * 50 for j in range(4) for i in range(6)]
        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 16, suffix=b"\x00" * 16)
        tables = scan_map_tables(data)

        # Every accepted table's byte range must be disjoint.
        ranges = sorted(
            (t.offset, t.offset + t.cols * t.rows * t.cell_width) for t in tables
        )
        for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
            assert e1 <= s2, f"overlap between {(s1, e1)} and {(s2, e2)}"

    def test_empty_input_returns_empty_list(self) -> None:
        assert scan_map_tables(b"") == []

    def test_detects_u8_cell_table(self) -> None:
        # 8-bit cells — common for older / smaller ECUs.
        x = [800, 1200, 1600, 2000, 2400, 2800]
        y = [10, 20, 30, 40]
        cells = [50 + i * 5 + j * 10 for j in range(len(y)) for i in range(len(x))]
        data = b"\x00" * 32 + _pack_u16(x) + _pack_u16(y) + bytes(cells) + b"\x00" * 32
        tables = scan_map_tables(data)
        u8 = [t for t in tables if t.cell_width == 1 and t.rows == 4]
        assert u8, f"expected u8 table, got: {tables}"
        assert u8[0].cols == 6

    def test_tolerates_alignment_padding(self) -> None:
        # Compiler emits 4-byte padding between axes and data.
        x = [800, 1200, 1600, 2000, 2400, 2800]
        y = [10, 20, 30, 40, 50]
        cells = [500 + i * 50 + j * 100 for j in range(5) for i in range(6)]
        data = (
            b"\x00" * 32
            + _pack_u16(x)
            + _pack_u16(y)
            + b"\x00" * 4  # alignment padding
            + _pack_u16(cells)
            + b"\x00" * 32
        )
        tables = scan_map_tables(data)
        assert tables
        t = tables[0]
        assert t.cols == 6 and t.rows == 5
        assert t.offset == 32 + 6 * 2 + 5 * 2 + 4  # padding accounted for

    def test_rejects_ascii_string_region(self) -> None:
        # Axes followed by an ASCII catalog — must not be promoted.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        ascii_blob = (b"Hello, world!  " * 8)[: len(x) * len(y) * 2]
        data = b"\x00" * 32 + _pack_u16(x) + _pack_u16(y) + ascii_blob + b"\x00" * 32
        tables = scan_map_tables(data)
        # ASCII surface must not become a u16 table with the same geometry.
        assert not any(
            t.cols == 6 and t.rows == 4 and t.cell_width == 2 for t in tables
        )

    def test_rejects_broadcast_row_stripe(self) -> None:
        # Every row identical to row 0 → stripe penalty zeros the score.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        row = [500, 550, 600, 650, 700, 750]
        cells = row * 4  # broadcast
        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 32, suffix=b"\x00" * 32)
        tables = scan_map_tables(data)
        assert not any(t.cols == 6 and t.rows == 4 for t in tables)

    def test_rejects_too_few_distinct_values(self) -> None:
        # Block uses only two distinct values — clamp region masquerade.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        cells = [100 if (i + j) % 2 == 0 else 200 for j in range(4) for i in range(6)]
        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 32, suffix=b"\x00" * 32)
        tables = scan_map_tables(data)
        # 2 distinct values out of 24 cells → ratio ~0.08 < 0.18 floor.
        assert not any(t.cols == 6 and t.rows == 4 for t in tables)

    def test_linear_axis_outscores_irregular_axis(self) -> None:
        # Two candidate tables: one with a perfectly linear X axis, one
        # with an irregular one but same data quality.
        x_lin = [1000, 1500, 2000, 2500, 3000, 3500]
        x_irr = [100, 250, 700, 1200, 1900, 2100]
        y = [10, 20, 30, 40]
        cells = [500 + i * 25 + j * 50 for j in range(4) for i in range(6)]
        block_lin = _make_2d_table(x_lin, y, cells)
        block_irr = _make_2d_table(x_irr, y, cells)
        data = b"\x00" * 16 + block_lin + b"\x00" * 32 + block_irr + b"\x00" * 16
        tables = scan_map_tables(data)
        # Both should be detected; the linear-axis one must score higher.
        lin_t = next((t for t in tables if t.x_axis_offset == 16 and t.cols == 6), None)
        irr_t = next(
            (
                t
                for t in tables
                if t.x_axis_offset == 16 + len(block_lin) + 32 and t.cols == 6
            ),
            None,
        )
        assert lin_t is not None and irr_t is not None
        assert lin_t.score > irr_t.score

    def test_recovers_greedy_absorbed_y_axis(self) -> None:
        x = [800, 1200, 1600, 2000, 2400, 2800]
        y = [10, 20, 30, 40]
        cells = [500 + i * 50 + j * 100 for j in range(4) for i in range(6)]
        data = _make_2d_table(x, y, cells, prefix=b"\x00" * 32, suffix=b"\x00" * 32)
        tables = scan_map_tables(data)
        canonical = next(
            (t for t in tables if t.cols == 6 and t.rows == 4 and t.cell_width == 2),
            None,
        )
        assert canonical is not None
        assert canonical.x_axis_offset == 32
        assert canonical.y_axis_offset == 32 + 6 * 2

    def test_large_buffer_scans_in_reasonable_time(self) -> None:
        import random
        import time

        rng = random.Random(0)
        body = bytearray(rng.randint(0, 255) for _ in range(16 * 1024))
        x = list(range(500, 500 + 8 * 100, 100))
        y = list(range(10, 10 + 6 * 10, 10))
        cells = [50 + i * 5 + j * 8 for j in range(6) for i in range(8)]
        block = _pack_u16(x) + _pack_u16(y) + _pack_u16(cells)
        body[4096 : 4096 + len(block)] = block
        t0 = time.perf_counter()
        tables = scan_map_tables(bytes(body))
        dt = time.perf_counter() - t0
        assert dt < 2.0, f"16 KB scan took {dt:.2f}s (regression budget 2 s)"
        assert any(
            t.cols == 8
            and t.rows == 6
            and t.x_axis_offset == 4096
            and t.cell_width == 2
            for t in tables
        )

    def test_uses_supplied_axis_list_without_rescanning(self) -> None:
        # Pass a hand-crafted axis list; even with otherwise-uninteresting
        # data the table search should run against it.
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        cells = [500 + i * 25 + j * 50 for j in range(4) for i in range(6)]
        data = _make_2d_table(x, y, cells)
        axes = [
            MapAxis(offset=0, length=6, byte_order="little", values=tuple(x)),
            MapAxis(offset=12, length=4, byte_order="little", values=tuple(y)),
        ]
        tables = scan_map_tables(data, axes=axes)
        assert tables
        assert isinstance(tables[0], MapTable)


# ============================================================================
# Shared-axis series probe tests
# ============================================================================


class TestSharedAxisSeries:
    """Consecutive shared-axis block detection (Pattern A)."""

    def test_detects_two_table_series(self):
        """[X][Y][data1][data2] → 2 tables, shared axes, disjoint data."""
        x = [0, 500, 1000, 1500, 2000, 2500, 3000, 3500]
        y = [0, 400, 800, 1200]
        # Use non-monotonic data so values aren't detected as axes themselves.
        cells1 = [(r * 7 + c * 13 + 50) % 200 + 50 for r in range(4) for c in range(8)]
        cells2 = [(r * 7 + c * 13 + 150) % 200 + 50 for r in range(4) for c in range(8)]
        data = _make_2d_table(x, y, cells1) + _pack_u16(cells2, "little")

        tables = scan_map_tables(data, min_score=0.4)
        # Should find anchor + 1 series member
        assert len(tables) >= 2, f"Expected >= 2 tables, got {len(tables)}"

        # Same axes
        x_offs = {t.x_axis_offset for t in tables}
        y_offs = {t.y_axis_offset for t in tables}
        assert len(x_offs) == 1, f"All tables should share x_axis_offset: {x_offs}"
        assert len(y_offs) == 1, f"All tables should share y_axis_offset: {y_offs}"

        # Disjoint data ranges
        ranges = sorted((t.offset, t.offset + t.cols * t.rows * t.cell_width) for t in tables)
        for i in range(len(ranges) - 1):
            assert ranges[i][1] <= ranges[i + 1][0], f"Data ranges overlap: {ranges}"

    def test_series_disabled_with_limit_one(self):
        """max_series_tables=1 → only the anchor, no series members."""
        x = [0, 500, 1000, 1500]
        y = [0, 400, 800, 1200]  # ≥4 values required by min_axis_length
        cells1 = [(r * 7 + c * 13 + 10) % 100 + 10 for r in range(4) for c in range(4)]
        cells2 = [(r * 7 + c * 13 + 60) % 100 + 10 for r in range(4) for c in range(4)]
        data = _make_2d_table(x, y, cells1) + _pack_u16(cells2, "little")

        tables_all = scan_map_tables(data, min_score=0.4)
        tables_one = scan_map_tables(data, max_series_tables=1, min_score=0.4)
        assert len(tables_one) == 1
        assert len(tables_all) >= 2

    @pytest.mark.xfail(reason="1D series needs data that scores above 0.78 "
                              "but isn't detected as a 2D axis — hard to synthetic")
    def test_1d_series_detection(self):
        """[X][v1][v2] → 2 1D tables sharing the X axis."""
        x = [0, 500, 1000, 1500, 2000, 2500]
        v1 = [120, 140, 160, 180, 200, 220]
        v2 = [320, 340, 360, 380, 400, 420]
        data = _pack_u16(x, "little") + _pack_u16(v1, "little") + _pack_u16(v2, "little")

        tables = scan_map_tables(data, min_score=0.3)
        tables_1d = [t for t in tables if t.rows == 1]
        assert len(tables_1d) >= 2, f"Expected >= 2 1D tables, got {len(tables_1d)}"
        x_offs = {t.x_axis_offset for t in tables_1d}
        assert len(x_offs) == 1
