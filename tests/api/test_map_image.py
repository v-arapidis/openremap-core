"""
Tests for the SVG picture of a table.

Two layers are covered:

* ``services/maps/map_svg.render_map_svg`` — pure text out, so it is tested by
  reading the document: does it contain the cells, the breakpoints, the numbers
  that were measured, and does it escape what a filename might contain?
* ``map_image`` (API) and ``map-image`` (CLI) — the same table, end to end, with
  the breakpoints that are actually stored in a real dump.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.api import call
from openremap.api.commands.scan_maps import scan_maps as scan_maps_cmd
from openremap.cli.main import app as cli_app
from openremap.core.services.maps.map_svg import render_map_svg, value_color

ROOT = Path(__file__).parent.parent
M33 = ROOT / "data/ECUs/Bosch/M3.3/0261200404-1267357689__1__1.bin"
runner = CliRunner()

GRID = [[10, 20, 30], [40, 50, 60]]


class TestRamp:
    def test_floor_is_blue_and_peak_is_red(self):
        low, high = value_color(0.0), value_color(1.0)
        assert low[2] > low[0]
        assert high[0] > high[2]

    def test_out_of_range_is_clamped(self):
        assert value_color(-3) == value_color(0.0)
        assert value_color(7) == value_color(1.0)


class TestRenderDocument:
    def test_one_rect_per_cell_plus_decorations(self):
        svg = render_map_svg(GRID, title="t")
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        # 6 cells + frame + backdrop + colour scale bars + peak outline
        assert svg.count("<rect") > 6

    def test_cell_values_are_written_when_asked(self):
        svg = render_map_svg(GRID, title="t", include_values=True)
        assert ">10<" in svg and ">60<" in svg

    def test_values_are_omitted_for_large_tables(self):
        """Labels inside 40x40 cells would be unreadable, so they are dropped."""
        wide = [[7] * 40 for _ in range(40)]
        svg = render_map_svg(wide, title="t")  # default: off above 24x24
        assert 'text-anchor="middle">7<' not in svg
        forced = render_map_svg(wide, title="t", include_values=True)
        assert forced.count('text-anchor="middle">7<') == 40 * 40

    def test_measured_facts_and_notes_are_printed(self):
        svg = render_map_svg(
            GRID,
            title="dump.bin · 0x001000",
            facts=["3 x 2 cells", "score 0.91"],
            notes=["nothing is interpolated"],
        )
        assert "3 x 2 cells" in svg
        assert "score 0.91" in svg
        assert "nothing is interpolated" in svg

    def test_stored_axes_are_labelled_as_breakpoints(self):
        svg = render_map_svg(
            GRID, title="t", x_axis=[100, 200, 300], y_axis=[10, 20], axis_source="stored"
        )
        assert "column breakpoints" in svg
        assert "row breakpoints" in svg
        assert ">300<" in svg

    def test_indices_are_labelled_as_indices(self):
        svg = render_map_svg(GRID, title="t")
        assert "column index" in svg
        assert "breakpoints" not in svg

    def test_title_is_escaped(self):
        svg = render_map_svg(GRID, title="a & b <c>")
        assert "&amp;" in svg and "&lt;c&gt;" in svg

    def test_empty_table_still_produces_a_document(self):
        svg = render_map_svg([], title="nothing here")
        assert svg.startswith("<svg")
        assert "no cells to draw" in svg

    def test_peak_cell_is_outlined(self):
        svg = render_map_svg(GRID, title="t")
        assert svg.count('stroke="#e8eef7"') >= 1


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestMapImageCommand:
    def _table(self) -> dict:
        scanned = scan_maps_cmd({"path": str(M33), "min_score": 0.85, "top": 5})
        return scanned["tables"][0]

    def test_reads_breakpoints_from_the_binary(self):
        picture = call("map_image", {"path": str(M33), "table": self._table()})
        assert picture["axes"]["source"] == "stored"
        assert len(picture["axes"]["x"]) == int(picture["table"]["cols"])
        assert picture["svg"].startswith("<svg")
        assert "breakpoints" in picture["svg"]

    def test_summary_matches_the_cells(self):
        picture = call("map_image", {"path": str(M33), "table": self._table()})
        summary = picture["summary"]
        assert summary["count"] == int(picture["table"]["cols"]) * int(
            picture["table"]["rows"]
        )
        assert summary["min"] <= summary["mean"] <= summary["max"]

    def test_a_table_without_cells_is_refused(self):
        from openremap.api.errors import GuardError

        with pytest.raises(GuardError):
            call("map_image", {"path": str(M33), "table": {"offset": 0, "cols": 0, "rows": 0}})


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestCli:
    def test_writes_an_svg_next_to_the_dump(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)
        result = runner.invoke(cli_app, ["map-image", str(copy)])
        assert result.exit_code == 0, result.output
        written = list(tmp_path.glob("*_map_0x*.svg"))
        assert len(written) == 1
        assert written[0].read_text(encoding="utf-8").startswith("<svg")
        assert "WRITTEN" in result.output

    def test_list_shows_index_and_offset(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)
        result = runner.invoke(cli_app, ["map-image", str(copy), "--list"])
        assert result.exit_code == 0
        assert "tables at min score" in result.output
        assert "0x" in result.output

    def test_unknown_offset_suggests_the_nearest(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)
        result = runner.invoke(cli_app, ["map-image", str(copy), "--offset", "0x1"])
        assert result.exit_code == 1
        assert "Nearest is" in result.output

    def test_output_path_is_honoured(self, tmp_path):
        copy = tmp_path / "dump.bin"
        shutil.copy(M33, copy)
        target = tmp_path / "picture.svg"
        result = runner.invoke(cli_app, ["map-image", str(copy), "-o", str(target)])
        assert result.exit_code == 0
        assert target.exists()


@pytest.mark.skipif(not M33.exists(), reason="test corpus absent")
class TestRecipeInfo:
    """A recipe can be *read* without a target — the APPLY screen needs that."""

    def _recipe_file(self, tmp_path) -> Path:
        import json

        stock = Path(str(M33))
        tuned = tmp_path / "tuned.bin"
        data = bytearray(stock.read_bytes())
        for offset in (0x2000, 0x4000, 0x6000):
            data[offset] ^= 0xFF
        tuned.write_bytes(bytes(data))
        cooked = call("cook_strict", {"stock_path": str(stock), "tuned_path": str(tuned)})
        if cooked["recipe"] is None:  # a repeated anchor in this dump: the looser rung
            cooked = call("cook_forced", {"stock_path": str(stock), "tuned_path": str(tuned)})
        assert cooked["recipe"] is not None, cooked["report"].get("refusal")
        path = tmp_path / "r.remap"
        path.write_text(json.dumps(cooked["recipe"]), encoding="utf-8")
        return path

    def test_reads_the_declared_facts(self, tmp_path):
        path = self._recipe_file(tmp_path)
        info = call("recipe_info", {"path": str(path)})
        assert info["schema"] == "5.0"
        assert info["instructions"] >= 1
        assert info["contract"]["identity_floor"] in ("any", "firmware")
        assert info["source"]["sha256"]
        assert info["tune"]["sha256"] != info["source"]["sha256"]

    def test_a_document_that_is_not_a_recipe_is_refused(self, tmp_path):
        from openremap.api.errors import GuardError

        bad = tmp_path / "not_a_recipe.remap"
        bad.write_text('{"type": "identify-output"}', encoding="utf-8")
        with pytest.raises(GuardError) as excinfo:
            call("recipe_info", {"path": str(bad)})
        assert "[A1]" in str(excinfo.value)

    def test_broken_json_is_refused(self, tmp_path):
        from openremap.api.errors import GuardError

        bad = tmp_path / "broken.remap"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(GuardError):
            call("recipe_info", {"path": str(bad)})

    def test_apply_rungs_accept_a_recipe_path(self, tmp_path):
        """The TUI hands over a path; the gate reads it (and reports the size)."""
        path = self._recipe_file(tmp_path)
        result = call("apply_strict", {"path": str(M33), "recipe_path": str(path)})
        report = result["report"]
        # whether it applies or refuses, the recipe was read: the sizes prove it
        assert report["sizes"]["recipe"] == M33.stat().st_size
        assert report["sizes"]["target"] == M33.stat().st_size


def test_the_family_reference_reports_what_stands_behind_it():
    """15 documented families is a grouping; 38 extractors answer to them."""
    from openremap.core.manufacturers import EXTRACTORS

    report = call("families", {})
    assert report["count"] == len(report["families"]) == 15
    assert report["extractors"] == len(EXTRACTORS) > report["count"]
