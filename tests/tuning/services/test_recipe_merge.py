"""Tests for openremap.core.services.recipe_merge."""

from __future__ import annotations

import random

import pytest
from openremap.core.services.recipes.patcher import ECUPatcher
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.recipe_merge import MergeConflict, merge_recipes


def _pair(patches_a: dict[int, int], patches_b: dict[int, int]) -> tuple[bytes, bytes, bytes]:
    """Random 8 KB stock with two differently-tuned variants.

    Seeded (not os.urandom): os.urandom made these tests flaky — if the
    random stock already contained a patch byte at the target offset, that
    variant produced no instruction and merges expected N but got N-1
    (~0.45% of CI runs, 2026-08-23).
    """
    stock = bytearray(random.Random(0).randbytes(8192))
    mod_a = bytearray(stock)
    mod_b = bytearray(stock)
    for off, val in patches_a.items():
        mod_a[off] = val
    for off, val in patches_b.items():
        mod_b[off] = val
    return bytes(stock), bytes(mod_a), bytes(mod_b)


def _cook(stock: bytes, mod: bytes) -> dict:
    analyzer = ECUDiffAnalyzer(
        original_data=stock,
        modified_data=mod,
        original_filename="stock.bin",
        modified_filename="tuned.bin",
    )
    return analyzer.build_recipe()


class TestMergeRecipes:
    def test_disjoint_edits_combine(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)

        merged = merge_recipes(a, b, stock_data=stock)

        assert len(merged["instructions"]) == len(a["instructions"]) + len(
            b["instructions"]
        )
        assert merged["metadata"]["merged_from"]
        assert merged["fingerprint"].startswith("sha256:")
        assert merged["schema_version"] == "4.4"  # re-annotated from stock

    def test_identical_edit_deduplicates(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {200: 0x11, 400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)

        merged = merge_recipes(a, b, stock_data=stock)
        offsets = [i["offset"] for i in merged["instructions"]]
        assert offsets.count(200) == 1
        assert 400 in offsets

    def test_same_offset_different_value_conflicts(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {200: 0x99})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)

        with pytest.raises(MergeConflict, match="Conflict at 0xC8"):
            merge_recipes(a, b, stock_data=stock)

    def test_overlapping_ranges_conflict(self) -> None:
        # Two adjacent-byte edits cook into different merged blocks in
        # each recipe: force overlap via manual instruction surgery.
        stock, mod_a, mod_b = _pair({200: 0x11, 202: 0x12}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)
        # Manually shift+stretch recipe B's first instruction so its range
        # overlaps A's edit with a different boundary (and keep ob valid
        # so it passes the stock validation).
        b["instructions"][0]["offset"] = 199
        b["instructions"][0]["size"] = 2
        b["instructions"][0]["ob"] = stock[199:201].hex().upper()

        with pytest.raises(MergeConflict, match="Overlapping edits"):
            merge_recipes(a, b, stock_data=stock)

    def test_instructions_mismatching_stock_are_skipped(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)
        # Corrupt one of B's instructions so it no longer matches the stock.
        b["instructions"][0]["ob"] = "DEAD"

        merged = merge_recipes(a, b, stock_data=stock)
        warnings = merged["ecu"]["cook_warnings"]
        assert any("skipped" in w for w in warnings)
        assert all(i["ob"] != "DEAD" for i in merged["instructions"])

    def test_strict_aborts_on_mismatching_instructions(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)
        b["instructions"][0]["ob"] = "DEAD"

        with pytest.raises(MergeConflict, match="strict"):
            merge_recipes(a, b, stock_data=stock, strict=True)

    def test_ecu_mismatch_refuses(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)
        # Synthetic random bins carry no match_key — inject distinct ones.
        a["ecu"]["match_key"] = "EDC17::AAAA"
        b["ecu"]["match_key"] = "EDC17::BBBB"

        with pytest.raises(MergeConflict, match="ECU mismatch"):
            merge_recipes(a, b, stock_data=stock)

    def test_without_stock_requires_identical_sha256(self) -> None:
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)

        # Same stock → identical sha256 → merge succeeds without --stock.
        merged = merge_recipes(a, b)
        assert len(merged["instructions"]) >= 2
        assert merged["schema_version"] == "4.3"  # no stock → no re-annotate

        b["ecu"]["sha256"] = "abc"
        with pytest.raises(MergeConflict, match="--stock"):
            merge_recipes(a, b)

    def test_non_unique_anchor_warning(self) -> None:
        # Zero-filled stock → every recipe must be built with
        # require_unique=False; the merged set re-check warns.
        stock = bytes(8192)
        mod_a = bytearray(stock)
        mod_a[200] = 0x11
        mod_b = bytearray(stock)
        mod_b[400] = 0x22
        a = ECUDiffAnalyzer(
            original_data=stock,
            modified_data=bytes(mod_a),
            original_filename="s.bin",
            modified_filename="t.bin",
            require_unique=False,
        ).build_recipe()
        b = ECUDiffAnalyzer(
            original_data=stock,
            modified_data=bytes(mod_b),
            original_filename="s.bin",
            modified_filename="t.bin",
            require_unique=False,
        ).build_recipe()

        merged = merge_recipes(a, b, stock_data=stock)
        assert any(
            "non-unique" in w for w in merged["ecu"]["cook_warnings"]
        )

    def test_volatile_section_dropped_with_warning(self) -> None:
        """A volatile (4.5) input's excluded-volatile evidence does not
        transfer to the merged recipe — it is dropped, with a warning."""
        stock, mod_a, mod_b = _pair({200: 0x11}, {400: 0x22})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)
        a["volatile"] = {
            "excluded": [
                {"index": 0, "offset": 100, "size": 2, "kind": "VIN",
                 "confidence": 0.95, "action": "excluded", "evidence": []}
            ],
            "flagged": [],
            "summary": {"excluded_count": 1, "flagged_count": 0,
                        "bytes_excluded": 2},
        }
        a["schema_version"] = "4.5"

        merged = merge_recipes(a, b, stock_data=stock)
        assert "volatile" not in merged
        assert any(
            "volatile section dropped" in w for w in merged["ecu"]["cook_warnings"]
        )
        # The merged instruction set still combines both recipes' edits.
        assert len(merged["instructions"]) == 2


class TestMergedRecipeApplies:
    def test_merged_equals_sequential_application(self) -> None:
        """The gold standard: applying the merged recipe to the stock must
        equal applying recipe A then recipe B sequentially."""
        stock, mod_a, mod_b = _pair({200: 0x11, 300: 0x33}, {400: 0x22, 500: 0x44})
        a = _cook(stock, mod_a)
        b = _cook(stock, mod_b)

        merged = merge_recipes(a, b, stock_data=stock)

        # Sequential: stock → A → B
        after_a = ECUPatcher(stock, a).apply_all()
        assert after_a is not None
        sequential = ECUPatcher(bytes(after_a), b).apply_all()
        assert sequential is not None

        # Merged: stock → merged
        direct = ECUPatcher(stock, merged).apply_all()
        assert direct is not None

        assert bytes(direct) == bytes(sequential)

        # And the merged result equals the union of both tune edits.
        expected = bytearray(stock)
        expected[200] = 0x11
        expected[300] = 0x33
        expected[400] = 0x22
        expected[500] = 0x44
        assert bytes(direct) == bytes(expected)
