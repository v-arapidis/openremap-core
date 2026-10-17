"""
Tests for the pure map CSV exporter (``services/maps/map_exporter.py``).

Two things are pinned here:

1. **The WinOLS grid format** — the CSV layout is an external contract (people
   open these files in WinOLS / Excel), so the exact text matters: 2D tables
   are ``",x1,x2…"`` header + ``y``-prefixed rows, 1D tables are two columns,
   and the file name encodes offset + geometry + cell width.
2. **Layer purity** — the service returns CSV *text*; it must not touch the
   filesystem.  Writing moved to the CLI (``_write_csv_files``), so the last
   test here asserts that a service call survives with ``open()`` sabotaged.
"""

from __future__ import annotations

import struct

import pytest

from openremap.core.services.maps.map_exporter import render_tables_csv
from openremap.core.services.maps.map_hunter import MapTable


def _u16(values: list[int], order: str = "little") -> bytes:
    fmt = ("<" if order == "little" else ">") + f"{len(values)}H"
    return struct.pack(fmt, *values)


def _table(**overrides) -> MapTable:
    base = dict(
        offset=8,
        cols=4,
        rows=3,
        cell_width=2,
        byte_order="little",
        x_axis_offset=0,
        y_axis_offset=100,
        score=0.9,
    )
    base.update(overrides)
    return MapTable(**base)


def _bin_2d(order: str = "little") -> bytes:
    """X axis @0 (4), 4x3 u16 cells @8, Y axis @100 (3)."""
    data = bytearray(120)
    data[0:8] = _u16([100, 200, 300, 400], order)
    data[8:32] = _u16([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], order)
    data[100:106] = _u16([10, 20, 30], order)
    return bytes(data)


class TestWinOlsGridFormat:
    def test_2d_table_is_header_plus_y_prefixed_rows(self):
        rendered = render_tables_csv(_bin_2d(), [_table()])
        assert rendered == {
            "0x00000008_4x3_u16.csv": ",100,200,300,400\n"
            "10,1,2,3,4\n"
            "20,5,6,7,8\n"
            "30,9,10,11,12\n"
        }

    def test_1d_table_is_two_columns(self):
        data = bytearray(32)
        data[0:6] = _u16([100, 200, 300])
        data[8:14] = _u16([7, 8, 9])
        table = _table(cols=3, rows=1, y_axis_offset=None)

        assert render_tables_csv(bytes(data), [table]) == {
            "0x00000008_3x1_u16.csv": "100,7\n200,8\n300,9\n"
        }

    def test_missing_y_axis_renders_two_columns(self):
        data = bytearray(32)
        data[0:4] = _u16([11, 22])
        data[8:12] = _u16([33, 44])
        table = _table(cols=2, rows=1, y_axis_offset=None)

        assert render_tables_csv(bytes(data), [table]) == {
            "0x00000008_2x1_u16.csv": "11,33\n22,44\n"
        }

    def test_cell_width_one_is_named_u8_and_decoded_as_bytes(self):
        data = bytearray(16)
        data[0:8] = _u16([1, 2, 3, 4])
        data[8:12] = bytes([10, 20, 30, 40])
        table = _table(rows=1, cell_width=1, y_axis_offset=None)

        assert render_tables_csv(bytes(data), [table]) == {
            "0x00000008_4x1_u8.csv": "1,10\n2,20\n3,30\n4,40\n"
        }

    def test_big_endian_table_reads_big_endian_values(self):
        rendered = render_tables_csv(_bin_2d("big"), [_table(byte_order="big")])
        assert list(rendered.values())[0].startswith(",100,200,300,400\n")

    def test_stride_skips_interleaved_row_padding(self):
        """Compound tables store their half every *stride* bytes."""
        data = bytearray(64)
        data[0:6] = _u16([1, 2, 3])           # X axis (3 cols)
        data[8:14] = _u16([10, 11, 12])       # row 0
        data[20:26] = _u16([20, 21, 22])      # row 1 — 12 bytes after row 0
        data[32:38] = _u16([30, 31, 32])      # row 2
        data[14:20] = _u16([99, 99, 99])      # padding that must be ignored
        data[26:32] = _u16([98, 98, 98])
        data[100 if False else 40:46] = _u16([5, 6, 7])   # Y axis @40
        table = _table(
            cols=3, rows=3, offset=8, x_axis_offset=0, y_axis_offset=40, stride=12
        )

        rendered = render_tables_csv(bytes(data), [table])
        assert list(rendered.values())[0] == (
            ",1,2,3\n5,10,11,12\n6,20,21,22\n7,30,31,32\n"
        )

    def test_line_endings_are_lf_only(self):
        text = list(render_tables_csv(_bin_2d(), [_table()]).values())[0]
        assert "\r" not in text and text.endswith("\n")


class TestRenderingContract:
    def test_one_entry_per_table_in_input_order(self):
        tables = [
            _table(offset=8),
            _table(offset=8, cols=2, rows=1, y_axis_offset=None),
        ]
        rendered = render_tables_csv(_bin_2d(), tables)
        assert list(rendered) == ["0x00000008_4x3_u16.csv", "0x00000008_2x1_u16.csv"]

    def test_no_tables_renders_nothing(self):
        assert render_tables_csv(b"", []) == {}

    def test_offset_is_part_of_the_file_name(self):
        wide = render_tables_csv(_bin_2d(), [_table(offset=8)])
        narrow = render_tables_csv(
            _bin_2d(), [_table(offset=32, rows=1, y_axis_offset=None)]
        )
        assert list(wide) == ["0x00000008_4x3_u16.csv"]
        assert list(narrow) == ["0x00000020_4x1_u16.csv"]

    def test_render_does_no_file_io(self, monkeypatch):
        """The service layer contract: bytes/dict in, no paths, no side effects.

        Writing CSV files is the CLI's job now (``_write_csv_files``); if this
        ever regresses, the test fails loudly instead of quietly re-mixing the
        layers.
        """

        def _boom(*args, **kwargs):
            raise AssertionError("map_exporter must not touch the filesystem")

        monkeypatch.setattr("builtins.open", _boom)
        monkeypatch.setattr("pathlib.Path.mkdir", _boom)
        monkeypatch.setattr("pathlib.Path.write_text", _boom)

        rendered = render_tables_csv(_bin_2d(), [_table()])
        assert list(rendered.values())[0].startswith(",100,200,300,400")
