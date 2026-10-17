"""
Tests for the synthetic dump generator (``core/synth/``).

The contract these pin:

* **Deterministic** — same parameters + seed give byte-identical output, so a
  corpus can store the parameters instead of the file.
* **Detectable** — every planted map is found *exactly* (offset, cols, rows,
  cell width, byte order) by the map hunter, and the segmenter labels the
  planted sectors as planted.  Without this the "ground truth" is worthless.
* **Anonymous** — ``identify`` reports ``Unknown``: a generated file has no
  manufacturer fingerprints, and that refusal to guess is a promise worth
  pinning (it is what makes these files usable as negative fixtures).
* **Tunable** — ``tuned()`` moves bytes only inside the planted cell blocks and
  records what it changed.
"""

from __future__ import annotations

import pytest

from openremap.core.services.identify.confidence import score_identity
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.synth import generate_dump
from openremap.core.synth.generator import _SHAPES

PALETTE = len(_SHAPES)
SIZE = 256 * 1024


def _find(tables, offset: int):
    return next((t for t in tables if t.offset == offset), None)


def _assert_all_maps_found(dump) -> None:
    tables = scan_map_tables(dump.data)
    for m in dump.maps:
        hit = _find(tables, m.offset)
        assert hit is not None, (
            f"planted map {m.map_id} ({m.cols}x{m.rows}) at {m.offset:#x} "
            f"was not detected at all"
        )
        assert (hit.cols, hit.rows, hit.cell_width, hit.byte_order) == (
            m.cols,
            m.rows,
            m.cell_width,
            m.byte_order,
        ), f"map {m.map_id} detected with the wrong shape"
        assert hit.x_axis_offset == m.x_axis_offset
        assert hit.y_axis_offset == m.y_axis_offset
        assert hit.score >= 0.55


class TestDeterminism:
    def test_same_parameters_give_identical_bytes(self):
        a = generate_dump(size=SIZE, maps=3, seed=7)
        b = generate_dump(size=SIZE, maps=3, seed=7)
        assert a.data == b.data
        assert a.sha256 == b.sha256
        assert a.maps == b.maps

    def test_different_seed_changes_the_background(self):
        a = generate_dump(size=SIZE, maps=2, seed=7)
        b = generate_dump(size=SIZE, maps=2, seed=8)
        assert a.data != b.data
        assert a.maps[0].cells == b.maps[0].cells, "map contents depend on shape, not seed"

    def test_tuned_is_deterministic(self):
        a = generate_dump(size=SIZE, maps=3, seed=7).tuned(percent=7)
        b = generate_dump(size=SIZE, maps=3, seed=7).tuned(percent=7)
        assert a.data == b.data
        assert a.changes == b.changes


class TestGeometry:
    def test_size_is_respected(self):
        assert len(generate_dump(size=64 * 1024).data) == 64 * 1024
        assert len(generate_dump(size=300 * 1024).data) == 300 * 1024

    def test_sectors_tile_the_whole_file_without_gaps(self):
        dump = generate_dump(size=SIZE, layout=("code", "calibration", "erased", "code"))
        assert [s.kind for s in dump.sectors] == ["code", "calibration", "erased", "code"]
        assert dump.sectors[0].start == 0
        assert dump.sectors[-1].end == len(dump.data)
        for left, right in zip(dump.sectors, dump.sectors[1:]):
            assert left.end == right.start, "sectors must be contiguous"

    def test_params_are_recorded_for_regeneration(self):
        dump = generate_dump(size=SIZE, maps=2, seed=99, cell_width=1, byte_order="big")
        assert dump.params == {
            "size": SIZE,
            "maps": 2,
            "seed": 99,
            "layout": ["code", "calibration", "erased", "code"],
            "cell_width": 1,
            "byte_order": "big",
        }
        rebuilt = generate_dump(**dump.params)
        assert rebuilt.data == dump.data

    def test_erased_sector_is_actually_blank(self):
        dump = generate_dump(size=SIZE)
        erased = next(s for s in dump.sectors if s.kind == "erased")
        assert set(dump.data[erased.start:erased.end]) == {0}

    def test_maps_land_inside_calibration_sectors(self):
        dump = generate_dump(size=SIZE, maps=4)
        for m in dump.maps:
            sec = dump.sectors[m.sector]
            assert sec.kind == "calibration"
            assert sec.start <= m.x_axis_offset < m.end <= sec.end

    def test_map_at_finds_a_planted_map(self):
        dump = generate_dump(size=SIZE, maps=2)
        target = dump.maps[1]
        assert dump.map_at(target.offset) == target
        assert dump.map_at(target.offset + 1) is None


class TestDetectable:
    def test_all_palette_shapes_are_found_exactly(self):
        """One dump per palette shape — the palette exists to be detectable."""
        dump = generate_dump(size=SIZE, maps=PALETTE, seed=11)
        assert [m.cols for m in dump.maps] == [c for c, _ in _SHAPES]
        _assert_all_maps_found(dump)

    @pytest.mark.parametrize("cell_width", [2, 1])
    def test_both_cell_widths_are_found(self, cell_width):
        dump = generate_dump(size=SIZE, maps=3, cell_width=cell_width)
        assert all(m.cell_width == cell_width for m in dump.maps)
        _assert_all_maps_found(dump)

    @pytest.mark.parametrize("byte_order", ["little", "big"])
    def test_both_byte_orders_are_found(self, byte_order):
        dump = generate_dump(size=SIZE, maps=3, byte_order=byte_order)
        assert all(m.byte_order == byte_order for m in dump.maps)
        _assert_all_maps_found(dump)

    def test_segmenter_labels_the_planted_sectors(self):
        dump = generate_dump(size=SIZE, maps=2, seed=7)
        kinds = [r.kind for r in segment(dump.data)]
        assert kinds == ["code", "calibration", "erased", "code"]
        calib = next(r for r in segment(dump.data) if r.kind == "calibration")
        assert calib.tables >= len(dump.maps), "planted maps should count as tables"
        erased = next(r for r in segment(dump.data) if r.kind == "erased")
        assert erased.fill_byte == 0
        assert erased.fill_ratio == 1.0

    def test_no_calibration_sector_without_maps_is_allowed(self):
        dump = generate_dump(size=SIZE, maps=0, layout=("code", "erased"))
        assert dump.maps == ()
        assert [s.kind for s in dump.sectors] == ["code", "erased"]


class TestAnonymity:
    def test_identify_reports_unknown(self):
        """Generated dumps carry no OEM fingerprints — and must not invent one."""
        dump = generate_dump(size=SIZE, maps=3, seed=7)
        result = identify_ecu(dump.data, "synthetic.bin")
        assert result["manufacturer"] is None
        assert result["ecu_family"] is None
        assert result["match_key"] is None
        assert score_identity(result, "synthetic.bin", dump.data).tier == "Unknown"

    def test_identity_is_stable_for_a_tuned_pair(self):
        """Anonymous stays anonymous: the tune must not conjure a family.

        The content hashes necessarily differ (the cells moved); every other
        identity field must be identical.
        """
        stock = generate_dump(size=SIZE, maps=2, seed=5)
        tuned = stock.tuned(percent=10)
        a = identify_ecu(stock.data, "s.bin")
        b = identify_ecu(tuned.data, "s.bin")
        hashes = {"md5", "sha256", "sha256_first_64kb"}
        assert {k: v for k, v in a.items() if k not in hashes} == {
            k: v for k, v in b.items() if k not in hashes
        }
        assert a["sha256"] != b["sha256"], "the tune must change the content hash"


class TestTuning:
    def test_tuned_changes_only_bytes_inside_planted_cells(self):
        stock = generate_dump(size=SIZE, maps=3, seed=7)
        tuned = stock.tuned(percent=8)
        assert stock.is_stock and not tuned.is_stock

        changed = [i for i, (a, b) in enumerate(zip(stock.data, tuned.data)) if a != b]
        assert changed, "a tune must actually move bytes"
        for offset in changed:
            assert any(m.offset <= offset < m.end for m in stock.maps), (
                f"byte {offset:#x} outside every planted cell block changed"
            )

    def test_tuning_records_cells_changed_per_map(self):
        dump = generate_dump(size=SIZE, maps=3, seed=7).tuned(percent=20)
        assert set(dump.changes) <= {0, 1, 2}
        for map_id, count in dump.changes.items():
            planted = dump.maps[map_id]
            assert 0 < count <= planted.cols * planted.rows

    def test_can_tune_a_subset_of_maps(self):
        stock = generate_dump(size=SIZE, maps=3, seed=7)
        tuned = stock.tuned(percent=10, maps=[1])
        assert set(tuned.changes) == {1}
        untouched = stock.maps[0]
        assert stock.data[untouched.offset:untouched.end] == tuned.data[
            untouched.offset:untouched.end
        ]

    def test_untouched_maps_keep_their_cells(self):
        stock = generate_dump(size=SIZE, maps=2, seed=7)
        tuned = stock.tuned(percent=10, maps=[0])
        assert tuned.maps[1] == stock.maps[1]


class TestValidation:
    def test_rejects_unsupported_parameters(self):
        for kwargs in (
            {"size": 1024},
            {"maps": -1},
            {"cell_width": 3},
            {"byte_order": "middle"},
            {"layout": ()},
            {"layout": ("code", "nonsense")},
        ):
            with pytest.raises(ValueError):
                generate_dump(**kwargs)  # type: ignore[arg-type]

    def test_rejects_maps_without_a_calibration_sector(self):
        with pytest.raises(ValueError, match="no calibration sector"):
            generate_dump(size=SIZE, maps=1, layout=("code", "erased"))

    def test_rejects_more_maps_than_the_sector_holds(self):
        with pytest.raises(ValueError, match="too small"):
            generate_dump(size=8192, maps=40, layout=("calibration",))
