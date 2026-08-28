"""
Tests for the ``diff-maps`` sub-command — cell diff math and end-to-end runs.

Covered:
    - ``_diff_cells``: normal percentages, 0→nonzero (inf) percentages,
      mixed finite/inf averages, unchanged cells, size mismatch error
    - ``_json_safe``: inf/-inf → "inf"/"-inf" strings for JSON
    - ``diff-maps`` end-to-end: realistic synthetic map, BE endian handling
"""

from __future__ import annotations

import json
import math
import random
import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.core.cli.commands.diff_maps import (
    _axes_similar,
    _diff_cells,
    _json_safe,
    _pearson,
)
from openremap.core.cli.main import app
from tests.conftest import make_layout_bin

runner = CliRunner()


# ---------------------------------------------------------------------------
# _diff_cells — unit tests
# ---------------------------------------------------------------------------


class TestDiffCells:
    def test_normal_percentages(self):
        r = _diff_cells([100, 200, 300], [110, 240, 300])
        assert r["max_abs"] == 40.0
        assert r["avg_abs"] == pytest.approx(16.67)  # (10 + 40 + 0) / 3
        assert r["max_pct"] == 20.0
        assert r["avg_pct"] == pytest.approx(10.0)  # (10 + 20 + 0) / 3
        assert r["changed_cells"] == 2
        assert r["total_cells"] == 3

    def test_all_cells_disabled_to_enabled_avg_pct_is_inf(self):
        """Every changed cell went 0 → nonzero: avg_pct must be inf, not 0."""
        r = _diff_cells([0, 0, 0, 0], [100, 200, 300, 400])
        assert r["max_pct"] == float("inf")
        assert r["avg_pct"] == float("inf")
        assert r["changed_cells"] == 4

    def test_mixed_inf_and_finite_percentages(self):
        r = _diff_cells([0, 100, 200], [500, 110, 200])
        assert r["max_pct"] == float("inf")
        assert r["avg_pct"] == 5.0  # finite pcts are 10% and 0% (unchanged)

    def test_unchanged_cells_are_finite_zero(self):
        r = _diff_cells([50, 50], [50, 50])
        assert r["max_pct"] == 0.0
        assert r["avg_pct"] == 0.0
        assert r["changed_cells"] == 0

    def test_cell_count_mismatch_returns_error(self):
        r = _diff_cells([1, 2, 3], [4, 5])
        assert "error" in r

    def test_json_safe_converts_inf(self):
        r = _diff_cells([0, 0], [7, 9])
        clean = _json_safe(r)
        assert clean["max_pct"] == "inf"
        assert clean["avg_pct"] == "inf"
        json.dumps(clean)  # must serialise


# ---------------------------------------------------------------------------
# End-to-end with a realistic synthetic map
# ---------------------------------------------------------------------------


def _make_map_bin(x_axis, y_axis, func, byte_order="<", cell_width=2, base=0x300):
    """Build a binary containing one map: X axis, Y axis, cells."""
    buf = bytearray(4096)
    fmt = lambda n: f"{byte_order}{n}H"
    off = base
    buf[off : off + 2 * len(x_axis)] = struct.pack(fmt(len(x_axis)), *x_axis)
    off += 2 * len(x_axis)
    buf[off : off + 2 * len(y_axis)] = struct.pack(fmt(len(y_axis)), *y_axis)
    off += 2 * len(y_axis)
    for yi in range(len(y_axis)):
        for xi in range(len(x_axis)):
            v = max(0, min(65535, int(func(xi, yi))))
            struct.pack_into(fmt(1), buf, off, v)
            off += 2
    return bytes(buf)


def _surface(xi, yi):
    return 600 + yi * 100 + int(900 * math.sin(xi / 2.1)) + 40 * ((xi * 7 + yi * 13) % 5)


_X = [300, 600, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000]
_Y = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


class TestDiffMapsEndToEnd:
    def test_matched_diff_on_realistic_map(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(_X, _Y, lambda xi, yi: _surface(xi, yi) + 12)

        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        m = out["matches"][0]
        assert m["max_abs"] > 0
        assert m["changed_cells"] > 0

    def test_identical_files_match_with_no_changes(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        sp = tmp_path / "stock.bin"
        sp.write_bytes(stock)

        result = runner.invoke(app, ["diff-maps", str(sp), str(sp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        assert all(m["changed_cells"] == 0 for m in out["matches"])

    def test_big_endian_map_diff(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface, byte_order=">", base=0x400)
        tuned = _make_map_bin(
            _X, _Y, lambda xi, yi: _surface(xi, yi) + 50, byte_order=">", base=0x400,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        m = out["matches"][0]
        assert m["byte_order"] == "big"
        assert m["max_abs"] > 0

    def test_partial_tune_is_not_suspicious(self, tmp_path):
        """A tune changing a minority of cells: aligned, not suspicious."""
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            _X, _Y,
            lambda xi, yi: _surface(xi, yi) + (80 if (xi, yi) in ((0, 0), (1, 0), (2, 1)) else 0),
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        for m in out["matches"]:
            if m["changed_cells"] > 0:
                assert m["suspicious"] is False
                return
        pytest.fail("expected at least one changed match")

    def test_misaligned_padding_realigns_grids(self, tmp_path):
        """Scanner padding ambiguity must not produce garbage diffs."""
        x_axis = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000]
        y_axis = [10, 20, 30, 40, 50]

        def add_map(zeros_at):
            buf = bytearray(8192)
            o = 0x400
            buf[o : o + 16] = struct.pack("<8H", *x_axis)
            o += 16
            buf[o : o + 10] = struct.pack("<5H", *y_axis)
            o += 10
            for yi in range(5):
                for xi in range(8):
                    v = 0 if (xi, yi) in zeros_at else _surface(xi, yi)
                    struct.pack_into("<H", buf, o, max(0, min(65535, v)))
                    o += 2
            return bytes(buf)

        zeros = ((0, 0), (1, 0), (2, 0), (7, 4))
        stock = add_map(zeros)
        tuned_bytes = bytearray(stock)
        # flip the three in-grid zeros to 900 at their true positions
        cell_off = 0x400 + 16 + 10
        for xi, yi in zeros:
            pos = cell_off + (yi * 8 + xi) * 2
            struct.pack_into("<H", tuned_bytes, pos, 900)

        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(bytes(tuned_bytes))

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        m = out["matches"][0]
        # Only 3 cells truly changed — not 32/32 garbage.
        assert m["changed_cells"] <= 6, f"got {m['changed_cells']} changed cells"
        assert m["suspicious"] is False

    def test_suspicious_flag_on_unrelated_grids(self, tmp_path):
        """Same axes but entirely different cells → suspicious flag."""
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            _X, _Y, lambda xi, yi: 4000 + yi * 300 + xi * 37,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matched_count"] >= 1
        assert any(m["suspicious"] for m in out["matches"])


class TestGrouping:
    """Maps sharing identical axis breakpoints are grouped in the output."""

    @staticmethod
    def _write_pair(tmp_path, stock_bufs, tuned_bufs):
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(b"".join(stock_bufs))
        tp.write_bytes(b"".join(tuned_bufs))
        return sp, tp

    @staticmethod
    def _multi_map(functions):
        """One 64KB binary pair: stock/tuned with several spaced maps."""
        stock_buf = bytearray(64 * 1024)
        tuned_buf = bytearray(64 * 1024)
        off = 0x400
        for (x, y, fn, delta) in functions:
            for buf, d in ((stock_buf, 0), (tuned_buf, delta)):
                buf[off : off + 2 * len(x)] = struct.pack(f"<{len(x)}H", *x)
                buf[off + 2 * len(x) : off + 2 * len(x) + 2 * len(y)] = struct.pack(
                    f"<{len(y)}H", *y
                )
                o = off + 2 * len(x) + 2 * len(y)
                for yi in range(len(y)):
                    for xj in range(len(x)):
                        v = max(0, min(65535, int(fn(xj, yi) + d)))
                        struct.pack_into("<H", buf, o, v)
                        o += 2
            off += 2 * len(x) + 2 * len(y) + 2 * len(x) * len(y) + 64
        return bytes(stock_buf), bytes(tuned_buf)

    def test_grouped_human_output(self, tmp_path):
        xa = [500, 1000, 1500, 2000, 2500, 3000]
        ya = [10, 20, 30, 40]
        xb = [800, 1200, 1600, 2000]

        stock, tuned = self._multi_map(
            [
                (xa, ya, lambda xi, yi: 600 + yi * 90 + xi * 5, 20),
                (xa, ya, lambda xi, yi: 900 + yi * 70 + xi * 7, 15),
                (xb, ya, lambda xi, yi: 750 + yi * 60 + xi * 9, 10),
            ]
        )
        sp, tp = self._write_pair(tmp_path, [stock], [tuned])

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp)])

        assert result.exit_code == 0
        assert "Group A — 2 map(s)" in result.stdout
        assert "Group B — 1 map(s)" in result.stdout

    def test_grouped_json_output(self, tmp_path):
        xa = [500, 1000, 1500, 2000, 2500, 3000]
        ya = [10, 20, 30, 40]
        xb = [800, 1200, 1600, 2000]

        stock, tuned = self._multi_map(
            [
                (xa, ya, lambda xi, yi: 600 + yi * 90 + xi * 5, 20),
                (xa, ya, lambda xi, yi: 900 + yi * 70 + xi * 7, 15),
                (xb, ya, lambda xi, yi: 750 + yi * 60 + xi * 9, 10),
            ]
        )
        sp, tp = self._write_pair(tmp_path, [stock], [tuned])

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert "groups" in out
        assert all("group" in m for m in out["matches"])
        counts = {g["id"]: g["count"] for g in out["groups"]}
        assert sum(counts.values()) == len(out["matches"])

    def test_compact_mode_limits_group_rows(self, tmp_path):
        xa = [500, 1000, 1500, 2000]
        ya = [10, 20, 30]

        stock, tuned = self._multi_map(
            [
                (xa, ya, lambda xi, yi: 600 + yi * 90 + xi * 5, 20),
                (xa, ya, lambda xi, yi: 900 + yi * 70 + xi * 7, 15),
                (xa, ya, lambda xi, yi: 750 + yi * 60 + xi * 9, 10),
                (xa, ya, lambda xi, yi: 820 + yi * 55 + xi * 11, 25),
            ]
        )
        sp, tp = self._write_pair(tmp_path, [stock], [tuned])

        result = runner.invoke(
            app, ["diff-maps", str(sp), str(tp), "--compact"],
        )

        assert result.exit_code == 0
        assert "Group A — 4 map(s)" in result.stdout
        assert "… and 1 more in this group" in result.stdout


# ---------------------------------------------------------------------------
# Compound tables — two maps sharing a Y axis
# ---------------------------------------------------------------------------


def _make_compound_bin(x1, x2, y, fn1, fn2, base=0x400):
    """[X1][X2][Y][row-interleaved cells] — the compound map-pair layout."""
    buf = bytearray(64 * 1024)
    o = base
    buf[o : o + 2 * len(x1)] = struct.pack(f"<{len(x1)}H", *x1)
    o += 2 * len(x1)
    buf[o : o + 2 * len(x2)] = struct.pack(f"<{len(x2)}H", *x2)
    o += 2 * len(x2)
    buf[o : o + 2 * len(y)] = struct.pack(f"<{len(y)}H", *y)
    o += 2 * len(y)
    for r in range(len(y)):
        for c in range(len(x1)):
            struct.pack_into("<H", buf, o, max(0, min(65535, fn1(r, c))))
            o += 2
        for c in range(len(x2)):
            struct.pack_into("<H", buf, o, max(0, min(65535, fn2(r, c))))
            o += 2
    return bytes(buf)


class TestCompoundDiff:
    def test_compound_pair_diffs_two_halves(self, tmp_path):
        """An 8+8 compound pair diffs as two independent strided maps."""
        x1 = [680, 685, 810, 925, 1045, 1120, 1255, 1280]
        x2 = [1330, 1531, 1660, 1825, 1970, 2148, 2315, 2365]
        y = [690, 715, 825, 955, 1070, 1175, 1270, 1310]

        def m1(r, c):  # high map
            return 1000 + r * 80 + c * 30
        def m2(r, c):  # low map
            return 300 + r * 40 + c * 15
        def m1_t(r, c):  # tuned: bottom-left enriched
            return m1(r, c) + (50 if r >= 3 and c <= 3 else 0)
        def m2_t(r, c):  # tuned: right half enriched
            return m2(r, c) + (40 if r >= 1 and c >= 4 else 0)

        stock = _make_compound_bin(x1, x2, y, m1, m2)
        tuned = _make_compound_bin(x1, x2, y, m1_t, m2_t)
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        split = [m for m in out["matches"] if m.get("stride")]
        assert len(split) == 2, f"expected 2 split matches, got {len(split)}"
        by_cols = {m["cols"]: m for m in split}
        assert set(by_cols) == {8, 8}
        m_high = sorted(split, key=lambda m: -m["max_abs"])[0]
        # find the halves by their X-axis range
        half1 = [m for m in split if m["offset_stock"] == min(m["offset_stock"] for m in split)][0]
        half2 = [m for m in split if m is not half1][0]
        assert half1["stride"] == 32
        assert half2["stride"] == 32
        # map1 (first half): 5 rows × 4 cols changed = 20; map2: 6 × 4 = 24
        counts = sorted(m["changed_cells"] for m in split)
        assert counts == [20, 28], f"got {counts}"
        assert half1["suspicious"] is False

    def test_compound_csv_export_splits_halves(self, tmp_path):
        """scan-maps --export writes the two halves as separate CSVs."""
        x1 = [680, 685, 810, 925, 1045, 1120, 1255, 1280]
        x2 = [1330, 1531, 1660, 1825, 1970, 2148, 2315, 2365]
        y = [690, 715, 825, 955, 1070, 1175, 1270, 1310]

        stock = _make_compound_bin(
            x1, x2, y,
            lambda r, c: 1000 + r * 80 + c * 30,
            lambda r, c: 300 + r * 40 + c * 15,
        )
        sp = tmp_path / "stock.bin"
        sp.write_bytes(stock)
        out_dir = tmp_path / "csv"

        result = runner.invoke(
            app, ["scan-maps", str(sp), "--export", str(out_dir), "--min-score", "0.4"],
        )

        assert result.exit_code == 0
        csvs = sorted(out_dir.glob("*.csv"))
        assert len(csvs) == 2, f"expected 2 CSV files, got {[c.name for c in csvs]}"
        # the first half's grid: X1 header + high-value cells
        high_csv = next(c for c in csvs if c.name.startswith("0x00000430"))
        lines = high_csv.read_text().splitlines()
        assert lines[0].startswith(",680,685,810")
        assert lines[1].startswith("690,1000")
        assert "1030" in lines[1]
        low_csv = next(c for c in csvs if c.name.startswith("0x00000440"))
        low_lines = low_csv.read_text().splitlines()
        assert low_lines[0].startswith(",1330,1531")
        assert low_lines[1].startswith("690,300")


class TestChangedBlockPromotion:
    """Changed bytes outside scanned tables are promoted to synthetic maps."""

    def test_promoted_flat_y_table(self, tmp_path):
        """A flat-Y table without axes must surface via changed-block promotion."""
        row = [1700, 2320, 2515, 2580, 2740, 2630, 2530, 2505, 2325, 2235]
        stock = struct.pack("<10H", *row) * 5 + b"\x00" * 64
        tuned = struct.pack("<10H", *[v + 150 for v in row]) * 5 + b"\x00" * 64
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        promoted = [m for m in out["matches"] if m.get("promoted")]
        assert len(promoted) == 1, f"got {len(promoted)} promoted"
        m = promoted[0]
        assert m["cols"] == 10
        assert m["rows"] == 5
        assert m["changed_cells"] == 50
        assert m["max_abs"] == 150.0

    def test_promoted_constant_map_zeroed(self, tmp_path):
        """A constant map the tuner zeroed must surface (-100% change)."""
        stock = struct.pack("<24H", *([3302] * 24)) + b"\x00" * 64
        tuned = b"\x00" * 48 + b"\x00" * 64
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        promoted = [m for m in out["matches"] if m.get("promoted")]
        assert len(promoted) == 1
        m = promoted[0]
        assert m["changed_cells"] == 24
        assert m["max_abs"] == 3302.0
        assert m["max_pct"] == -100.0
        assert m["suspicious"] is False

    def test_promoted_marker_in_human_output(self, tmp_path):
        row = [1700, 2320, 2515, 2580, 2740, 2630, 2530, 2505, 2325, 2235]
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(struct.pack("<10H", *row) * 5 + b"\x00" * 64)
        tp.write_bytes(struct.pack("<10H", *[v + 150 for v in row]) * 5 + b"\x00" * 64)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp)])

        assert result.exit_code == 0
        assert "changed-block" in result.stdout
        assert "no-axis" in result.stdout


# ---------------------------------------------------------------------------
# Recipe cross-reference (--recipe / --annotate)
# ---------------------------------------------------------------------------


class TestRecipeCrossReference:
    def _pair(self, tmp_path, extra_changes=()):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = bytearray(_make_map_bin(
            _X, _Y, lambda xi, yi: _surface(xi, yi) + 80 if xi == 2 else _surface(xi, yi),
        ))
        for off, val in extra_changes:
            tuned[off] = val
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(bytes(tuned))
        return sp, tp

    def _cook(self, tmp_path, sp, tp, name="tune.remap"):
        result = runner.invoke(
            app,
            ["cook", str(sp), str(tp), "--output", str(tmp_path / name), "--compact"],
        )
        assert result.exit_code == 0
        return tmp_path / name

    def test_recipe_covers_all_changed_cells(self, tmp_path):
        sp, tp = self._pair(tmp_path)
        recipe_path = self._cook(tmp_path, sp, tp)

        result = runner.invoke(
            app,
            ["diff-maps", str(sp), str(tp), "--recipe", str(recipe_path), "--json"],
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        r = out["recipe"]
        assert r["instructions"] > 0
        assert r["maps_touched"] >= 1
        assert r["untracked_changed_cells"] == 0
        cov = [m for m in out["matches"] if m.get("recipe_cells_covered")]
        assert cov, "at least one match must carry recipe coverage"

    def test_untracked_change_is_reported(self, tmp_path):
        # Cook the recipe first (without the extra change), then add an
        # extra modification to the tuned file that the recipe does not
        # contain.
        sp, tp = self._pair(tmp_path)
        recipe_path = self._cook(tmp_path, sp, tp)

        tuned = bytearray(tp.read_bytes())
        tuned[0x3A0] = (tuned[0x3A0] + 7) & 0xFF
        tp.write_bytes(bytes(tuned))

        result = runner.invoke(
            app,
            ["diff-maps", str(sp), str(tp), "--recipe", str(recipe_path), "--json"],
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["recipe"]["untracked_changed_cells"] > 0

    def test_human_output_shows_cross_reference(self, tmp_path):
        sp, tp = self._pair(tmp_path)
        recipe_path = self._cook(tmp_path, sp, tp)

        result = runner.invoke(
            app,
            ["diff-maps", str(sp), str(tp), "--recipe", str(recipe_path)],
        )
        assert result.exit_code == 0
        assert "Recipe cross-reference" in result.stdout
        assert "Untracked:" in result.stdout

    def test_annotate_writes_4_4_recipe(self, tmp_path):
        sp, tp = self._pair(tmp_path)
        recipe_path = self._cook(tmp_path, sp, tp)
        out_path = tmp_path / "annotated.remap"

        result = runner.invoke(
            app,
            [
                "diff-maps",
                str(sp),
                str(tp),
                "--recipe",
                str(recipe_path),
                "--annotate",
                str(out_path),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(out_path.read_text())
        assert data["schema_version"] == "4.4"
        assert "maps" in data
        n_inst = len(data["instructions"])
        for m in data["maps"]:
            assert all(1 <= r <= n_inst for r in m["instruction_refs"])

    def test_missing_recipe_file_exits_two(self, tmp_path):
        # Click enforces exists=True on the option → usage error (2).
        sp, tp = self._pair(tmp_path)

        result = runner.invoke(
            app,
            [
                "diff-maps",
                str(sp),
                str(tp),
                "--recipe",
                str(tmp_path / "nope.remap"),
            ],
        )
        assert result.exit_code == 2

    def test_malformed_recipe_exits_one(self, tmp_path):
        sp, tp = self._pair(tmp_path)
        bad = tmp_path / "bad.remap"
        bad.write_text('{"not": "a recipe"}')

        result = runner.invoke(
            app,
            ["diff-maps", str(sp), str(tp), "--recipe", str(bad)],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Correlation helpers — unit tests
# ---------------------------------------------------------------------------


class TestPearson:
    def test_perfect_positive_correlation(self):
        assert _pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)

    def test_perfect_negative_correlation(self):
        assert _pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)

    def test_unrelated_series_low_correlation(self):
        r = _pearson([1, 2, 3, 4, 5], [100, 0, 50, 200, 10])
        assert r is not None and abs(r) < 0.5

    def test_constant_input_is_none(self):
        assert _pearson([5, 5, 5], [1, 2, 3]) is None

    def test_length_mismatch_is_none(self):
        assert _pearson([1, 2], [1, 2, 3]) is None


class TestAxesSimilar:
    def test_identical_axes(self):
        assert _axes_similar((300, 600, 1000), (300, 600, 1000), 0.15)

    def test_small_breakpoint_shift(self):
        assert _axes_similar((300, 600, 1000), (280, 590, 990), 0.15)

    def test_completely_different_axis(self):
        assert not _axes_similar((300, 600, 1000), (10, 20, 30), 0.15)

    def test_different_length(self):
        assert not _axes_similar((300, 600), (300, 600, 1000), 0.15)

    def test_empty_axis(self):
        assert not _axes_similar((), (), 0.15)


# ---------------------------------------------------------------------------
# Near-match — tables whose axis breakpoints changed
# ---------------------------------------------------------------------------


class TestNearMatchAxisChanged:
    """Maps whose axis breakpoints changed pair up via correlation."""

    def _run(self, tmp_path, x_axis, y_axis, func):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(x_axis, y_axis, func)
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)
        return runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

    def test_axis_shifted_map_is_near_matched(self, tmp_path):
        """Breakpoints moved a little + a normal retune → matched, flagged."""
        result = self._run(
            tmp_path,
            [v - 20 for v in _X],
            [v - 2 for v in _Y],
            lambda xi, yi: _surface(xi, yi) + 12,
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        near = [m for m in out["matches"] if m.get("near_match")]
        assert len(near) == 1, f"got {len(near)} near-matches"
        m = near[0]
        assert m["axis_changed"] is True
        assert m["correlation"] >= 0.95
        assert m["changed_cells"] > 0
        assert m["axis_stock"]["x"] != m["axis_tuned"]["x"]
        assert m["axis_stock"]["y"] != m["axis_tuned"]["y"]
        assert out["only_in_tuned_count"] == 0

    def test_unrelated_cells_do_not_near_match(self, tmp_path):
        """Similar axes but unrelated grids must stay unmatched."""
        result = self._run(
            tmp_path,
            [v - 20 for v in _X],
            [v - 2 for v in _Y],
            lambda xi, yi: 4000 + yi * 300 + xi * 37,
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert not any(m.get("near_match") for m in out["matches"])
        assert out["only_in_tuned_count"] >= 1

    def test_completely_different_axes_do_not_near_match(self, tmp_path):
        """A genuinely different axis (tiny range) must not pair up."""
        small = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150]
        result = self._run(
            tmp_path,
            small,
            _Y,
            lambda xi, yi: _surface(xi, yi) + 12,
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert not any(m.get("near_match") for m in out["matches"])

    def test_human_output_marks_axis_changed(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            [v - 20 for v in _X], [v - 2 for v in _Y],
            lambda xi, yi: _surface(xi, yi) + 12,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp)])

        assert result.exit_code == 0
        assert "axes changed" in result.stdout

    def test_strided_near_match_reads_cells_with_stride(self, tmp_path):
        """A compound (strided) map with rescaled axes must be diffed with its stride.

        Regression: the near-match pass used to read cells contiguously,
        mixing the two interleaved map halves (the exact-match path always
        read strided halves correctly).
        """
        x1 = [680, 685, 810, 925, 1045, 1120, 1255, 1280]
        x2 = [1330, 1531, 1660, 1825, 1970, 2148, 2315, 2365]
        y = [690, 715, 825, 955, 1070, 1175, 1270, 1310]

        def m1(r, c):
            return 1000 + r * 80 + c * 30
        def m2(r, c):
            return 300 + r * 40 + c * 15
        def m1_t(r, c):  # tuned: bottom-left enriched (5×4 = 20 cells)
            return m1(r, c) + (50 if r >= 3 and c <= 3 else 0)
        def m2_t(r, c):  # tuned: right half enriched (7×4 = 28 cells)
            return m2(r, c) + (40 if r >= 1 and c >= 4 else 0)

        stock = _make_compound_bin(x1, x2, y, m1, m2)
        tuned = _make_compound_bin(
            [v - 20 for v in x1], [v - 20 for v in x2], [v - 2 for v in y],
            m1_t, m2_t,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        near = [m for m in out["matches"] if m.get("near_match")]
        assert len(near) == 2, f"got {len(near)} near-matches"
        # both halves are strided; changed counts are computed WITH the stride
        assert all(m["stride"] == 32 for m in near)
        counts = sorted(m["changed_cells"] for m in near)
        assert counts == [20, 28], (
            f"got {counts} — strided halves were diffed contiguously"
        )

    def test_many_same_shape_near_matches_pair_correctly(self, tmp_path):
        """Pre-index: N same-shape maps with rescaled axes each pair with
        their own stock map (no cross-pairing)."""
        offsets = [0x400, 0x8000, 0x10000, 0x18000]
        stock_buf = bytearray(128 * 1024)
        tuned_buf = bytearray(128 * 1024)
        for i, base in enumerate(offsets):
            scale = 1 + i  # distinct axis values per map
            x = [300 * scale + j * 400 * scale for j in range(8)]
            y = [10 * scale + j * 15 * scale for j in range(5)]
            for buf, shift in ((stock_buf, 0), (tuned_buf, -1)):
                o = base
                buf[o : o + 16] = struct.pack(
                    "<8H", *(v + shift * 20 * scale for v in x)
                )
                o += 16
                buf[o : o + 10] = struct.pack(
                    "<5H", *(v + shift * 2 * scale for v in y)
                )
                o += 10
                for yi in range(5):
                    for xi in range(8):
                        struct.pack_into(
                            "<H", buf, o, 500 + yi * 100 + xi * 30 + i * 50,
                        )
                        o += 2
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(bytes(stock_buf))
        tp.write_bytes(bytes(tuned_buf))

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        near = [m for m in out["matches"] if m.get("near_match")]
        assert len(near) == len(offsets), f"got {len(near)} near-matches"
        # one-to-one: every stock table pairs with the tuned table at the
        # SAME data offset (no cross-pairing between the four maps)
        assert sorted(m["offset_stock"] for m in near) == sorted(
            m["offset_tuned"] for m in near
        )
        assert all(m["offset_delta"] == 0 for m in near)
        # axes were detected as changed for every pair
        assert all(m["axis_changed"] for m in near)


# ---------------------------------------------------------------------------
# Correlation-refined suspicion
# ---------------------------------------------------------------------------


class TestCorrelationSuspicion:
    """Correlation refines the suspicious flag: retunes ≠ different maps."""

    def test_heavy_retune_is_not_suspicious(self, tmp_path):
        """100% of cells changed but the grid correlates ~1.0 → same map."""
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            _X, _Y, lambda xi, yi: int(_surface(xi, yi) * 1.2 + 300),
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        m = out["matches"][0]
        # Near-total change (a few cells coincidentally map to the same
        # int) — enough to trip the old >90% suspicion heuristic.
        assert m["changed_cells"] / m["total_cells"] > 0.9
        assert m["correlation"] >= 0.9
        assert m["suspicious"] is False

    def test_correlation_field_present_on_exact_matches(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            _X, _Y, lambda xi, yi: _surface(xi, yi) + 12,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["matches"][0]["correlation"] is not None


# ---------------------------------------------------------------------------
# Changed but not identified
# ---------------------------------------------------------------------------


class TestUnidentifiedChanged:
    """Changed bytes outside any matched table are reported explicitly."""

    def _pair(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = bytearray(_make_map_bin(
            _X, _Y, lambda xi, yi: _surface(xi, yi) + 12,
        ))
        # Inject a changed region that is not a table: deterministic random
        # bytes (seeded — never os.urandom, per repo CI rules).  Must sit
        # INSIDE the 4 KiB buffer — the Rust diff ignores the tail beyond
        # min(len(original), len(modified)).
        blob = random.Random(1234).randbytes(32)
        tuned[0x800 : 0x800 + 32] = blob
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(bytes(tuned))
        return sp, tp

    def test_unidentified_region_reported_in_json(self, tmp_path):
        sp, tp = self._pair(tmp_path)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["unidentified_changed_count"] >= 1
        hit = [
            r for r in out["unidentified_changed"]
            if 0x800 <= r["offset"] < 0x800 + 32
        ]
        assert hit, f"expected blob at 0x800, got {out['unidentified_changed']}"
        assert hit[0]["size"] >= 32
        # The map itself must still be matched, not unidentified.
        assert any(m["cols"] == len(_X) for m in out["matches"])

    def test_unidentified_section_in_human_output(self, tmp_path):
        sp, tp = self._pair(tmp_path)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp)])

        assert result.exit_code == 0
        assert "Changed but not identified" in result.stdout

    def test_clean_pair_has_no_unidentified(self, tmp_path):
        stock = _make_map_bin(_X, _Y, _surface)
        tuned = _make_map_bin(
            _X, _Y, lambda xi, yi: _surface(xi, yi) + 12,
        )
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(stock)
        tp.write_bytes(tuned)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["unidentified_changed_count"] == 0


# ---------------------------------------------------------------------------
# Calibration-region default — layout consumers
# ---------------------------------------------------------------------------


class TestCalibrationDefault:
    """diff-maps defaults both scans to the detected calibration region."""

    def _pair(self, tmp_path):
        sp = tmp_path / "stock.bin"
        tp = tmp_path / "tuned.bin"
        sp.write_bytes(make_layout_bin(seed=7, map_delta=0))
        tp.write_bytes(make_layout_bin(seed=7, map_delta=12))
        return sp, tp

    def test_default_filters_both_files(self, tmp_path):
        """Code-sector junk tables are hidden; the real map still diffs."""
        sp, tp = self._pair(tmp_path)

        result = runner.invoke(app, ["diff-maps", str(sp), str(tp), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["stock_tables_hidden"] == 3
        assert out["tuned_tables_hidden"] == 3
        assert out["stock_tables"] == 23
        assert out["matched_count"] == 23
        # the real calibration map is matched and shows the +12 tune
        real = [
            m for m in out["matches"]
            if 0x11000 <= m["offset_stock"] < 0x12000
        ]
        assert real, "real calibration map must be matched"
        assert real[0]["changed_cells"] > 0
        # no junk from the code sector
        assert not any(m["offset_stock"] >= 0x30000 for m in out["matches"])

    def test_whole_file_includes_code_junk(self, tmp_path):
        sp, tp = self._pair(tmp_path)

        result = runner.invoke(
            app, ["diff-maps", str(sp), str(tp), "--whole-file", "--json"],
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["stock_tables_hidden"] == 0
        assert out["tuned_tables_hidden"] == 0
        assert out["matched_count"] == 26
        assert any(m["offset_stock"] >= 0x30000 for m in out["matches"])

    def test_real_pair_hides_code_tables(self, tmp_path):
        """Corpus-gated: the real EDC17 pair hides tables by default."""
        base = Path(__file__).parent.parent / "data" / "tune"
        stock = base / "original.bin"
        tuned = base / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"
        if not (stock.exists() and tuned.exists()):
            pytest.skip("tests/data/tune corpus pair missing")

        result = runner.invoke(app, ["diff-maps", str(stock), str(tuned), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["stock_tables_hidden"] > 0
        assert out["tuned_tables_hidden"] > 0
