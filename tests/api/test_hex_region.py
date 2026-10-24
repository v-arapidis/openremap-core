"""
Tests for the ``hex_region`` command and the ``map_image`` axis override.

``hex_region`` exists so the DIFF screen can show the bytes of a region no table
explains without any widget opening a file. Two things must hold: the range is
reported honestly (short reads included), and a range past the end is a refusal
rather than an empty string that looks like "nothing changed here".
"""

from __future__ import annotations

import pytest

from openremap.api import call
from openremap.api.errors import GuardError


def _binary(tmp_path, data: bytes):
    path = tmp_path / "dump.bin"
    path.write_bytes(data)
    return str(path)


class TestHexRegion:
    def test_returns_the_range_as_hex(self, tmp_path):
        path = _binary(tmp_path, bytes(range(32)))
        result = call("hex_region", {"path": path, "offset": 4, "length": 4})
        assert result["offset"] == 4
        assert result["length"] == 4
        assert result["hex"] == "04050607"

    def test_a_short_read_is_reported_as_short(self, tmp_path):
        """The caller asked for 8 at offset 30 of a 32-byte file."""
        path = _binary(tmp_path, bytes(range(32)))
        result = call("hex_region", {"path": path, "offset": 30, "length": 8})
        assert result["requested"] == 8
        assert result["length"] == 2
        assert len(result["hex"]) == 4

    def test_past_the_end_is_a_refusal(self, tmp_path):
        path = _binary(tmp_path, b"\x00" * 16)
        with pytest.raises(GuardError, match="past the end"):
            call("hex_region", {"path": path, "offset": 16, "length": 4})

    def test_the_api_lists_it(self):
        from openremap.api import list_methods

        assert "hex_region" in {m["name"] for m in list_methods()}


class TestMapImageAxisOverride:
    def test_the_callers_breakpoints_win(self, tmp_path):
        """A caller that already measured the axis must not have it re-derived."""
        table = [[1, 2], [3, 4]]
        data = bytearray(b"\x00" * 0x100)
        for index, value in enumerate([cell for row in table for cell in row]):
            data[0x40 + index * 2 : 0x40 + index * 2 + 2] = value.to_bytes(2, "little")
        path = _binary(tmp_path, bytes(data))
        descriptor = {
            "offset": 0x40,
            "cols": 2,
            "rows": 2,
            "cell_width": 2,
            "byte_order": "little",
        }
        without = call("map_image", {"path": path, "table": descriptor})["svg"]
        with_axis = call(
            "map_image",
            {
                "path": path,
                "table": {**descriptor, "x_axis": [11, 22], "y_axis": [33, 44]},
            },
        )["svg"]
        assert "11" in with_axis and "22" in with_axis
        assert without != with_axis
