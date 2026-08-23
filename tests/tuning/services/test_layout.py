"""Unit tests for openremap.core.services.layout — synthetic fixtures."""

from __future__ import annotations

import math
import os
import struct

from openremap.core.services.maps.layout import (
    find_ident_blocks,
    segment,
)
from openremap.core.services.maps.map_hunter import scan_map_tables


def _pack_u16(values: list[int]) -> bytes:
    fmt = "<" + "H" * len(values)
    return struct.pack(fmt, *values)


def _map_block(x_axis: list[int], y_axis: list[int], func) -> bytes:
    """One high-scoring 2D table: X axis, Y axis, cells (sin-based surface)."""
    cells = []
    for yi in range(len(y_axis)):
        for xi in range(len(x_axis)):
            v = max(
                0,
                min(
                    65535,
                    int(600 + yi * 100 + 900 * math.sin(xi / 2.1) + 40 * ((xi * 7 + yi * 13) % 5)),
                ),
            )
            cells.append(v)
    return _pack_u16(x_axis) + _pack_u16(y_axis) + _pack_u16(cells)


class TestSegmentSynthetic:
    def test_erased_fill_detection_ff(self) -> None:
        data = b"\xFF" * (0x10000 * 3)
        regions = segment(data, sector_size=0x10000)
        assert len(regions) == 1
        assert regions[0].kind == "erased"
        assert regions[0].fill_byte == 0xFF
        assert regions[0].start == 0 and regions[0].end == len(data)

    def test_erased_fill_detection_00(self) -> None:
        data = b"\x00" * 0x8000
        regions = segment(data, sector_size=0x4000)
        assert len(regions) == 1 and regions[0].kind == "erased"
        assert regions[0].fill_byte == 0x00

    def test_erased_fill_detection_exotic_byte(self) -> None:
        # EDC15-style: the erase byte is 0xC3, not FF/00.
        data = b"\xC3" * 0x10000
        regions = segment(data, sector_size=0x4000)
        assert len(regions) == 1 and regions[0].kind == "erased"
        assert regions[0].fill_byte == 0xC3

    def test_code_calibration_erased_layout(self) -> None:
        sec = 0x10000
        x = list(range(300, 300 + 12 * 400, 400))
        y = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        map_data = _map_block(x, y, None)

        code = bytearray(os.urandom(sec))
        cal = bytearray(os.urandom(sec))
        cal[0 : len(map_data)] = map_data
        data = bytes(code) + bytes(cal) + b"\xFF" * sec

        regions = segment(data, sector_size=sec)
        kinds = [r.kind for r in regions]
        assert kinds == ["code", "calibration", "erased"], kinds
        assert regions[1].tables_high_conf >= 1

    def test_adjacent_same_kind_merge(self) -> None:
        data = b"\xFF" * (0x10000 * 2) + os.urandom(0x10000)
        regions = segment(data, sector_size=0x10000)
        assert len(regions) == 2
        assert regions[0].kind == "erased" and regions[0].size == 0x20000
        assert regions[1].kind == "code"

    def test_mixed_fallback_for_partial_sector(self) -> None:
        # Half filled, half not — below the erased threshold, low entropy.
        data = b"\x00" * (0x8000) + os.urandom(0x8000)
        regions = segment(data, sector_size=0x10000)
        assert regions[0].kind == "mixed"
        assert regions[0].confidence == 0.3

    def test_empty_data(self) -> None:
        assert segment(b"") == []
        assert find_ident_blocks(b"") == []

    def test_sector_selection(self) -> None:
        # Alternating erased/code prevents same-kind merging so the
        # sector count is observable.
        big = (b"\xFF" * 0x10000 + os.urandom(0x10000)) * 8  # 16 sectors
        assert len(segment(big, tables=[])) == 16
        small = (b"\xFF" * 0x4000 + os.urandom(0x4000))  # 2 sectors
        assert len(segment(small, tables=[])) == 2

    def test_tables_reuse_matches_internal_scan(self) -> None:
        data = bytearray(os.urandom(0x20000))
        x = list(range(300, 300 + 10 * 400, 400))
        y = [10, 20, 30, 40, 50, 60, 70, 80]
        map_data = _map_block(x, y, None)
        data[0x100 : 0x100 + len(map_data)] = map_data

        tables = scan_map_tables(bytes(data), min_score=0.55, max_series_tables=16)
        with_tables = segment(bytes(data), tables=tables)
        internal = segment(bytes(data))
        assert with_tables == internal

    def test_deterministic(self) -> None:
        data = os.urandom(0x10000)
        assert segment(data) == segment(data)


class TestIdentBlocks:
    def test_exact_run_detection(self) -> None:
        prefix = os.urandom(0x100) + b"\x00"  # separator: run cannot extend back
        ident = b"1037501234  0261209352  EDC17C66  SAMPLE-IDENT-BLOCK!!!"  # 57 B
        # pad with spaces to cross the 64-byte minimum
        ident = ident + b" " * 20
        data = prefix + ident + b"\x00" + os.urandom(0x100)
        blocks = find_ident_blocks(data)
        assert len(blocks) == 1
        assert blocks[0].start == len(prefix)
        assert blocks[0].end == len(prefix) + len(ident)
        assert blocks[0].kind == "ident"

    def test_short_runs_ignored(self) -> None:
        data = os.urandom(0x100) + b"SHORT" + os.urandom(0x100)
        assert find_ident_blocks(data) == []

    def test_min_run_parameter(self) -> None:
        data = b"\x00" * 8 + b"A" * 20 + b"\x00" * 8
        assert find_ident_blocks(data, min_run=20)
        assert find_ident_blocks(data, min_run=21) == []

    def test_multiple_runs(self) -> None:
        data = os.urandom(16) + b"A" * 80 + os.urandom(32) + b"B" * 80 + os.urandom(16)
        blocks = find_ident_blocks(data)
        assert len(blocks) == 2
        assert blocks[0].start < blocks[1].start
