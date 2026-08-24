"""
Unit tests for flash-layout region tagging of recipe instructions.

Covers ``tag_instruction_regions``: calibration edits stay unflagged,
code/erased edits get the CODE_AREA flag, the no-calibration-signal
fallback tags everything ``unknown`` without flagging, and empty recipes
are a no-op.
"""

from __future__ import annotations

from tests.conftest import make_bin, make_instruction, make_layout_bin, make_recipe

from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.recipe_regions import tag_instruction_regions

# Offsets inside the make_layout_bin fixture (256 KB, seed 7):
#   sector 0 0x000000-0x010000 random -> code
#   sector 1 0x010000-0x020000 random fill + real map at 0x11032 -> calibration
#   sector 2 0x020000-0x030000 zeros   -> erased
#   sector 3 0x030000-0x040000 random  -> code
_CAL_OFF = 0x11032
_CODE_OFF = 0x34500
_ERASED_OFF = 0x25000


def _recipe_with(offsets: list[int]) -> dict:
    return make_recipe(
        [make_instruction(off, "AABB", "CCDD") for off in offsets]
    )


def _flag_kinds(inst: dict) -> list[str]:
    return [f.get("kind") for f in inst.get("flags", [])]


class TestTagInstructionRegions:
    def test_calibration_edit_is_safe(self):
        recipe = _recipe_with([_CAL_OFF])
        summary = tag_instruction_regions(recipe, make_layout_bin())
        inst = recipe["instructions"][0]
        assert inst["region"] == "calibration"
        assert "CODE_AREA" not in _flag_kinds(inst)
        assert summary == {
            "tagged": 1,
            "risky": 0,
            "by_region": {"calibration": 1},
        }

    def test_code_edit_gets_code_area_flag(self):
        recipe = _recipe_with([_CODE_OFF])
        summary = tag_instruction_regions(recipe, make_layout_bin())
        inst = recipe["instructions"][0]
        assert inst["region"] == "code"
        assert "CODE_AREA" in _flag_kinds(inst)
        flag = next(f for f in inst["flags"] if f["kind"] == "CODE_AREA")
        assert flag["confidence"] == 1.0
        assert flag["action"] == "REVIEW"
        assert summary["risky"] == 1

    def test_erased_edit_gets_code_area_flag(self):
        recipe = _recipe_with([_ERASED_OFF])
        summary = tag_instruction_regions(recipe, make_layout_bin())
        assert recipe["instructions"][0]["region"] == "erased"
        assert "CODE_AREA" in _flag_kinds(recipe["instructions"][0])
        assert summary["risky"] == 1

    def test_mixed_edits_counted_and_flag_appended_once(self):
        recipe = _recipe_with([_CAL_OFF, _CODE_OFF, _ERASED_OFF])
        summary = tag_instruction_regions(recipe, make_layout_bin())
        assert summary["tagged"] == 3
        assert summary["risky"] == 2
        assert summary["by_region"] == {
            "calibration": 1,
            "code": 1,
            "erased": 1,
        }
        # idempotent: a second pass must not duplicate the flag
        tag_instruction_regions(recipe, make_layout_bin())
        code_inst = next(
            i for i in recipe["instructions"] if i["offset"] == _CODE_OFF
        )
        assert _flag_kinds(code_inst).count("CODE_AREA") == 1

    def test_no_calibration_signal_tags_unknown_and_flags_nothing(self):
        """All-erased data (no calibration signal) -> unknown, no flags."""
        recipe = _recipe_with([0x200, 0x800])
        summary = tag_instruction_regions(recipe, make_bin(4096))
        for inst in recipe["instructions"]:
            assert inst["region"] == "unknown"
            assert "CODE_AREA" not in _flag_kinds(inst)
        assert summary["risky"] == 0
        assert summary["by_region"] == {"unknown": 2}

    def test_empty_instructions_are_noop(self):
        recipe = make_recipe([])
        summary = tag_instruction_regions(recipe, make_layout_bin())
        assert summary == {"tagged": 0, "risky": 0, "by_region": {}}
        assert "instructions" not in recipe or recipe["instructions"] == []

    def test_existing_flags_are_preserved(self):
        recipe = _recipe_with([_CODE_OFF])
        recipe["instructions"][0]["flags"] = [
            {"kind": "WEAK_ANCHOR", "confidence": 0.9, "action": "REVIEW"}
        ]
        tag_instruction_regions(recipe, make_layout_bin())
        kinds = _flag_kinds(recipe["instructions"][0])
        assert "WEAK_ANCHOR" in kinds
        assert "CODE_AREA" in kinds

    def test_precomputed_tables_are_reused(self):
        """Passing tables skips the internal scan and gives the same result."""
        data = make_layout_bin()
        tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
        recipe = _recipe_with([_CAL_OFF, _CODE_OFF])

        tag_instruction_regions(recipe, data, tables=tables)

        insts = recipe["instructions"]
        assert insts[0]["region"] == "calibration"
        assert insts[1]["region"] == "code"
        assert "CODE_AREA" in _flag_kinds(insts[1])

    def test_tagged_counts_only_instructions_with_offset(self):
        """An instruction without an offset is skipped, not overcounted."""
        recipe = make_recipe([make_instruction(_CAL_OFF, "AABB", "CCDD")])
        recipe["instructions"].append({"ob": "EE", "mb": "FF"})  # no offset

        summary = tag_instruction_regions(recipe, make_layout_bin())

        assert summary["tagged"] == 1
        assert summary["by_region"] == {"calibration": 1}
