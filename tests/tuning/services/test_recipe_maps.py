"""Tests for openremap.core.services.recipe_maps — the schema 4.4 maps layer."""

from __future__ import annotations

import struct

import os

from openremap.core.services.maps.map_hunter import MapTable, scan_map_tables
from openremap.core.services.recipes.patcher import ECUPatcher
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.recipe_maps import (
    MAPS_SCHEMA_VERSION,
    attach_maps,
    instruction_hits_table,
    table_cell_rows,
)


def _pack_u16(values: list[int], byte_order: str = "little") -> bytes:
    fmt = ("<" if byte_order == "little" else ">") + "H" * len(values)
    return struct.pack(fmt, *values)


def _make_2d_table(
    x_axis: list[int],
    y_axis: list[int],
    cells: list[int],
    *,
    prefix: bytes = b"",
    suffix: bytes = b"",
) -> bytes:
    return (
        prefix
        + _pack_u16(x_axis)
        + _pack_u16(y_axis)
        + _pack_u16(cells)
        + suffix
    )


def _instruction(offset: int, size: int = 2) -> dict:
    return {"offset": offset, "size": size, "ob": "00" * size, "mb": "FF" * size}


# ---------------------------------------------------------------------------
# Containment geometry
# ---------------------------------------------------------------------------


class TestContainment:
    def test_contiguous_table_rows(self) -> None:
        t = MapTable(100, 4, 3, 2, "little", 80, 88, 0.9, None)
        assert table_cell_rows(t) == [(100, 124)]

    def test_compound_strided_rows(self) -> None:
        t = MapTable(100, 4, 2, 2, "little", 80, 88, 0.9, 16)
        assert table_cell_rows(t) == [(100, 108), (116, 124)]

    def test_instruction_hits_contiguous(self) -> None:
        t = MapTable(100, 4, 3, 2, "little", 80, 88, 0.9, None)
        # structural region = X axis (80) .. data end (124), ±4 pad
        assert instruction_hits_table(104, 2, t) is True   # data cell
        assert instruction_hits_table(82, 2, t) is True    # X axis bytes
        assert instruction_hits_table(124, 2, t) is True   # pad slop at end
        assert instruction_hits_table(128, 2, t) is False  # beyond region
        assert instruction_hits_table(60, 2, t) is False   # before region

    def test_instruction_in_strided_gap_misses(self) -> None:
        # Gap between rows belongs to the interleaved partner half.
        t = MapTable(100, 4, 2, 2, "little", 80, 88, 0.9, 16)
        assert instruction_hits_table(104, 2, t) is True
        assert instruction_hits_table(108, 2, t) is False  # partner row
        assert instruction_hits_table(120, 2, t) is True


# ---------------------------------------------------------------------------
# attach_maps
# ---------------------------------------------------------------------------


def _simple_recipe(instructions: list[dict]) -> dict:
    return {
        "schema_version": "4.3",
        "type": "recipe",
        "metadata": {},
        "ecu": {"ecu_family": "EDC17"},
        "instructions": instructions,
    }


class TestAttachMaps:
    def test_maps_attached_and_schema_bumped(self) -> None:
        x = list(range(500, 500 + 8 * 500, 500))  # 8 values, step 500
        y = [0, 10, 25, 40]
        cells = [100 + r * 10 + c for r in range(4) for c in range(8)]
        data = b"\x00" * 32 + _make_2d_table(x, y, cells)
        table_data_off = 32 + 8 * 2 + 4 * 2  # prefix + axes

        recipe = _simple_recipe(
            [_instruction(table_data_off), _instruction(2)]  # inside table, outside
        )
        result = attach_maps(recipe, data)

        assert result["schema_version"] == MAPS_SCHEMA_VERSION
        assert result["metadata"]["annotated_maps"] is True
        assert len(result["maps"]) == 1
        m = result["maps"][0]
        assert m["cols"] == 8 and m["rows"] == 4 and m["cell_width"] == 2
        assert m["instruction_refs"] == [1]  # only the in-table instruction
        assert m["x_axis"]["values"] == x
        assert m["y_axis"]["values"] == y
        assert "label" in m and "label_confidence" in m

    def test_attach_maps_accepts_precomputed_tables(self) -> None:
        """Precomputed scan tables are used instead of a second scan."""
        x = list(range(500, 500 + 8 * 500, 500))
        y = [0, 10, 25, 40]
        cells = [100 + r * 10 + c for r in range(4) for c in range(8)]
        data = b"\x00" * 32 + _make_2d_table(x, y, cells)
        table_data_off = 32 + 8 * 2 + 4 * 2

        tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
        recipe = _simple_recipe([_instruction(table_data_off)])
        result = attach_maps(recipe, data, tables=tables)

        assert len(result["maps"]) == 1
        assert result["maps"][0]["instruction_refs"] == [1]

    def test_instruction_outside_all_maps_yields_empty_maps(self) -> None:
        x = list(range(0, 8 * 100, 100))
        y = [0, 50, 100]
        cells = [1] * 24
        data = b"\x00" * 32 + _make_2d_table(x, y, cells)

        recipe = _simple_recipe([_instruction(0, 2)])
        result = attach_maps(recipe, data)

        assert result["schema_version"] == MAPS_SCHEMA_VERSION
        assert result["maps"] == []

    def test_compound_halves_get_distinct_refs(self) -> None:
        # Two maps sharing a Y axis with interleaved rows (item 13 layout).
        # Fixture values proven to trigger the Rust split pass (see
        # TestCompoundSplitting in test_map_hunter.py).
        x1 = [680, 685, 810, 925, 1045, 1120, 1255, 1280]
        x2 = [1330, 1531, 1660, 1825, 1970, 2148, 2315, 2365]
        y = [690, 715, 825, 955, 1070, 1175, 1270, 1310]
        cells1 = [1000 + r * 80 + c * 30 for r in range(8) for c in range(8)]
        cells2 = [300 + r * 40 + c * 15 for r in range(8) for c in range(8)]
        rows: list[int] = []
        for r in range(8):
            rows.extend(cells1[r * 8 : (r + 1) * 8])
            rows.extend(cells2[r * 8 : (r + 1) * 8])
        data = (
            b"\x00" * 32
            + _pack_u16(x1)
            + _pack_u16(x2)
            + _pack_u16(y)
            + _pack_u16(rows)
        )
        data_off = 32 + 8 * 2 + 8 * 2 + 8 * 2  # prefix + X1 + X2 + Y
        # Half A: rows at data_off + r*32, half B: rows at data_off + 16 + r*32
        inst_a = _instruction(data_off + 2)
        inst_b = _instruction(data_off + 16 + 4)

        recipe = _simple_recipe([inst_a, inst_b])
        result = attach_maps(recipe, data)

        refs_a = [m for m in result["maps"] if 1 in m["instruction_refs"]]
        refs_b = [m for m in result["maps"] if 2 in m["instruction_refs"]]
        assert refs_a and refs_b, "each half must claim its own instruction"
        assert refs_a[0]["stride"] is not None
        assert refs_b[0]["stride"] is not None
        assert refs_a[0]["id"] != refs_b[0]["id"]

    def test_recipe_shape_untouched_outside_maps(self) -> None:
        x = list(range(0, 8 * 100, 100))
        y = [0, 50, 100]
        cells = [1] * 24
        data = b"\x00" * 32 + _make_2d_table(x, y, cells)
        table_data_off = 32 + 8 * 2 + 3 * 2

        recipe = _simple_recipe([_instruction(table_data_off)])
        before = {
            k: v for k, v in recipe.items() if k not in ("schema_version", "metadata")
        }
        result = attach_maps(recipe, data)

        for k, v in before.items():
            assert result[k] == v, f"field {k} changed by attach_maps"


# ---------------------------------------------------------------------------
# Patcher compatibility — 4.4 recipes patch exactly like 4.3
# ---------------------------------------------------------------------------


class TestPatcherCompatibility:
    def test_four_four_recipe_applies_identically(self) -> None:
        stock = bytearray(os.urandom(8192))
        off = 512
        x = list(range(0, 4 * 100, 100))
        y = [0, 50, 100]
        cells = [100 + r * 10 + c for r in range(3) for c in range(4)]
        import struct

        stock[off : off + 8] = struct.pack("<" + "H" * 4, *x)
        stock[off + 8 : off + 14] = struct.pack("<" + "H" * 3, *y)
        stock[off + 14 : off + 38] = struct.pack("<" + "H" * 12, *cells)
        tuned = bytearray(stock)
        tuned[off + 16] ^= 0xFF
        tuned[off + 20] ^= 0xFF

        analyzer = ECUDiffAnalyzer(
            original_data=bytes(stock),
            modified_data=bytes(tuned),
            original_filename="stock.bin",
            modified_filename="tuned.bin",
        )
        recipe = analyzer.build_recipe()
        attach_maps(recipe, bytes(stock))
        assert recipe["schema_version"] == "4.4"

        result = ECUPatcher(bytes(stock), recipe).apply_all()
        assert result is not None
        assert bytes(result) == bytes(tuned)

    def test_four_five_recipe_applies_identically(self) -> None:
        """A 4.5 recipe (with a ``volatile`` section) patches EXACTLY like
        a 4.4 one — consumers ignore the volatile key by design."""
        stock = bytearray(os.urandom(8192))
        tuned = bytearray(stock)
        tuned[512] ^= 0xFF
        tuned[600] ^= 0xFF

        analyzer = ECUDiffAnalyzer(
            original_data=bytes(stock),
            modified_data=bytes(tuned),
            original_filename="stock.bin",
            modified_filename="tuned.bin",
        )
        recipe = analyzer.build_recipe()
        # Simulate a cook-volatile output: schema 4.5 + volatile section.
        recipe["schema_version"] = "4.5"
        recipe["volatile"] = {
            "excluded": [],
            "flagged": [],
            "summary": {"excluded_count": 0, "flagged_count": 0,
                        "bytes_excluded": 0},
        }

        result = ECUPatcher(bytes(stock), recipe).apply_all()
        assert result is not None
        assert bytes(result) == bytes(tuned)
