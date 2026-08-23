"""Tests for openremap.core.services.audit."""

from __future__ import annotations

import os
import random

import pytest

from openremap.core.services.recipes.audit import audit
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer, compute_fingerprint
from openremap.core.services.recipes.volatile import classify_volatile


def _pair(patches: dict[int, int]) -> tuple[bytes, bytes]:
    """Random 8 KB stock + tuned variant.

    Seeded (not os.urandom): os.urandom made these tests flaky — if the
    random stock already contained a patch byte at the target offset, that
    variant produced no instruction (~0.45% of CI runs, 2026-08-23).
    """
    stock = bytearray(random.Random(0).randbytes(8192))
    mod = bytearray(stock)
    for off, val in patches.items():
        mod[off] = val
    return bytes(stock), bytes(mod)


def _cook(stock: bytes, mod: bytes, require_unique: bool = True) -> dict:
    return ECUDiffAnalyzer(
        original_data=stock,
        modified_data=mod,
        original_filename="stock.bin",
        modified_filename="tuned.bin",
        require_unique=require_unique,
    ).build_recipe()


class TestAuditClean:
    def test_honest_pair_is_clean(self) -> None:
        stock, tuned = _pair({200: 0x11, 400: 0x22})
        recipe = _cook(stock, tuned)

        result = audit(stock, tuned, recipe)
        assert result.clean is True
        assert result.provenance_ok and result.fingerprint_ok
        assert result.unaccounted_blocks == []
        assert result.unaccounted_bytes == 0

    def test_provenance_fails_with_different_stock(self) -> None:
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)
        other_stock = bytearray(os.urandom(8192))

        result = audit(bytes(other_stock), tuned, recipe)
        assert result.provenance_ok is False
        assert not result.clean
        assert any("provenance" in w for w in result.warnings)

    def test_fingerprint_fails_on_tampered_tuned(self) -> None:
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)
        tampered = bytearray(tuned)
        tampered[700] ^= 0xFF  # extra edit the recipe knows nothing about

        result = audit(stock, bytes(tampered), recipe)
        assert result.fingerprint_ok is False
        assert not result.clean

    def test_unaccounted_block_is_located(self) -> None:
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)
        tampered = bytearray(tuned)
        tampered[700] ^= 0xFF
        tampered[701] ^= 0xFF

        result = audit(stock, bytes(tampered), recipe)
        assert any(b.offset == 700 and b.size == 2 for b in result.unaccounted_blocks)
        assert result.unaccounted_bytes == 2

    def test_extra_edit_near_recipe_edit_stays_separate(self) -> None:
        # An unaccounted edit inside a recipe-touched region must still be
        # reported (the recipe explains its own bytes, not the extras).
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)
        tampered = bytearray(tuned)
        tampered[500] ^= 0xFF

        result = audit(stock, bytes(tampered), recipe)
        assert result.unaccounted_blocks

    def test_zero_fill_pair_works_with_non_unique_recipe(self) -> None:
        # Zero-filled stock (non-unique anchors) must not abort the audit.
        stock = bytes(8192)
        tuned = bytearray(stock)
        tuned[300] = 0x11
        recipe = _cook(stock, bytes(tuned), require_unique=False)

        result = audit(stock, bytes(tuned), recipe)
        assert result.fingerprint_ok is True


class TestAuditErrors:
    def test_size_mismatch_raises(self) -> None:
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)

        with pytest.raises(ValueError, match="different sizes"):
            audit(stock, tuned + b"\x00", recipe)

    def test_unapplicable_recipe_skips_verdict_3_with_warning(self) -> None:
        stock, tuned = _pair({200: 0x11})
        recipe = _cook(stock, tuned)
        # Corrupt an instruction so the patcher cannot apply it.
        recipe["instructions"][0]["ob"] = "DEAD"

        result = audit(stock, tuned, recipe)
        assert result.unaccounted_blocks == []
        assert any("skipped" in w for w in result.warnings)

    def test_old_schema_raises(self) -> None:
        stock, tuned = _pair({200: 0x11})
        with pytest.raises(ValueError, match="schema"):
            audit(stock, tuned, {"schema_version": "3.0", "instructions": []})


# ---------------------------------------------------------------------------
# Fingerprint stability — the invariant verdict 2 relies on
# ---------------------------------------------------------------------------


class TestFingerprintStability:
    """The fingerprint covers ONLY (offset, ob, mb) — metadata, creator
    timestamps, and the maps annotation layer are excluded by design, so a
    re-cook of the same pair always matches, regardless of recipe churn."""

    def _recipe(self):
        stock = bytearray(os.urandom(8192))
        tuned = bytearray(stock)
        tuned[200] ^= 0xFF
        return _cook(bytes(stock), bytes(tuned)), stock

    def test_metadata_changes_do_not_affect_fingerprint(self):
        recipe, _ = self._recipe()
        original = recipe["fingerprint"]

        recipe["creator"]["created_at"] = "1999-01-01T00:00:00Z"
        recipe["creator"]["name"] = "someone else"
        recipe["metadata"]["name"] = "renamed"
        recipe["metadata"]["description"] = "completely different text"
        recipe["metadata"]["tags"] = ["x", "y"]
        recipe["statistics"] = {}

        assert compute_fingerprint(recipe["instructions"]) == original

    def test_maps_layer_changes_do_not_affect_fingerprint(self):
        recipe, _ = self._recipe()
        original = recipe["fingerprint"]

        recipe["maps"] = []
        assert compute_fingerprint(recipe["instructions"]) == original

        recipe["maps"] = [
            {
                "id": "m1",
                "offset": 100,
                "cols": 4,
                "rows": 4,
                "label": "fuel",
                "label_confidence": 0.99,
                "instruction_refs": [1],
            }
        ]
        assert compute_fingerprint(recipe["instructions"]) == original

    def test_instruction_value_change_breaks_fingerprint(self):
        recipe, _ = self._recipe()
        original = recipe["fingerprint"]

        inst = recipe["instructions"][0]
        inst["mb"] = inst["ob"]  # pretend the edit was reverted
        assert compute_fingerprint(recipe["instructions"]) != original

    def test_instruction_offset_change_breaks_fingerprint(self):
        recipe, _ = self._recipe()
        original = recipe["fingerprint"]

        recipe["instructions"][0]["offset"] += 1
        assert compute_fingerprint(recipe["instructions"]) != original


# ---------------------------------------------------------------------------
# Volatile recipes — subset fingerprint + re-verify (schema 4.5)
# ---------------------------------------------------------------------------

# Real-shaped VIN (valid check digit '3', VW WMI) — mirrors the fixture in
# tests/cli/test_cli_cook_volatile.py so the volatile classifier excludes it.
VIN = "WVWZZZ1J3XW123456"
_PAD_A1 = (
    b"ecu identification block primary vin record one alpha beta gamma "
    b"delta epsilon zeta eta theta iota kappa "
)
_PAD_A2 = (
    b"end of primary vin record section lambda mu nu xi omicron pi rho "
    b"sigma tau upsilon phi chi psi omega "
)
_PAD_B1 = (
    b"mirror vin record block secondary copy part two alpha beta gamma "
    b"delta epsilon zeta eta theta iota kappa lambda mu "
)
_PAD_B2 = (
    b"end of mirror record nu xi omicron pi rho sigma tau upsilon phi "
    b"chi psi omega alpha beta gamma delta "
)
_VIN_OFF = 0x400
_CALIB_OFF = 0x1800


def _vin_pair(seed: int = 7) -> tuple[bytes, bytes, int]:
    """Random stock with an ident-block VIN + mirror, tuned by rewriting
    the VIN serial and flipping a calibration byte.  Returns
    (stock, tuned, serial_off)."""
    rng = random.Random(seed)
    stock = bytearray(rng.randbytes(8192))
    block = _PAD_A1 + VIN.encode() + _PAD_A2
    stock[_VIN_OFF : _VIN_OFF + len(block)] = block
    mirror = _PAD_B1 + VIN.encode() + _PAD_B2
    stock[0x600 : 0x600 + len(mirror)] = mirror

    serial_off = _VIN_OFF + len(_PAD_A1) + 11
    tuned = bytearray(stock)
    tuned[serial_off : serial_off + 6] = b"654321"
    tuned[_CALIB_OFF] ^= 0xFF
    return bytes(stock), bytes(tuned), serial_off


def _cook_volatile(stock: bytes, tuned: bytes) -> dict:
    """Build a schema-4.5 volatile recipe via the services, mirroring the
    ``cook-volatile`` CLI (no CLI dependency in a service test)."""
    recipe = ECUDiffAnalyzer(
        original_data=stock,
        modified_data=tuned,
        original_filename="stock.bin",
        modified_filename="tuned.bin",
    ).build_recipe()

    report = classify_volatile(recipe, stock)
    excluded_idx = {f.index for f in report.excluded}
    kept = [
        inst for i, inst in enumerate(recipe["instructions"]) if i not in excluded_idx
    ]

    recipe["instructions"] = kept
    recipe["fingerprint"] = compute_fingerprint(kept)
    recipe["statistics"] = {
        "total_changes": len(kept),
        "total_bytes_changed": sum(i["size"] for i in kept),
    }
    recipe["metadata"]["instruction_count"] = len(kept)
    recipe["metadata"]["source"] = "cook_volatile"
    recipe["metadata"]["excluded_volatile"] = True
    recipe["volatile"] = {
        "excluded": [f.to_dict() for f in report.excluded],
        "flagged": [f.to_dict() for f in report.flagged],
        "summary": {
            "excluded_count": len(report.excluded),
            "flagged_count": len(report.flagged),
            "bytes_excluded": sum(f.size for f in report.excluded),
        },
    }
    recipe["schema_version"] = "4.5"
    return recipe


class TestAuditVolatile:
    def test_honest_volatile_recipe_is_clean(self) -> None:
        stock, tuned, serial_off = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        assert recipe["volatile"]["summary"]["excluded_count"] == 1

        result = audit(stock, tuned, recipe)
        assert result.volatile_recipe is True
        assert result.provenance_ok is True
        assert result.fingerprint_ok is True
        # The VIN rewrite was deliberately excluded — NOT "unaccounted".
        assert result.unaccounted_blocks == []
        assert result.unaccounted_bytes == 0
        assert result.clean is True

    def test_volatile_subset_fabricated_instruction_fails(self) -> None:
        """A volatile recipe claiming an instruction NOT in the diff is a
        subset violation."""
        stock, tuned, _ = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        # Fabricate an extra instruction at an offset the diff never touched.
        recipe["instructions"].append(
            {
                "offset": 0x50,
                "size": 1,
                "ob": stock[0x50 : 0x51].hex().upper(),
                "mb": "FF",
                "ctx": "",
                "context_after": "",
            }
        )
        recipe["fingerprint"] = compute_fingerprint(recipe["instructions"])

        result = audit(stock, tuned, recipe)
        assert result.fingerprint_ok is False
        assert any("subset mismatch" in w for w in result.warnings)

    def test_volatile_reexclude_failure(self) -> None:
        """An excluded offset that no longer classifies as volatile fails
        re-verify."""
        stock, tuned, _ = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        # Claim a non-volatile offset (a random data byte) was excluded.
        recipe["volatile"]["excluded"] = [
            {"offset": 0x50, "size": 1, "kind": "VIN", "confidence": 0.95,
             "action": "excluded", "evidence": []}
        ]
        recipe["volatile"]["summary"]["excluded_count"] = 1

        result = audit(stock, tuned, recipe)
        assert result.fingerprint_ok is False
        assert any("re-verify failed" in w for w in result.warnings)

    def test_volatile_empty_excluded_fails_with_warning(self) -> None:
        """A 4.5 recipe with an empty excluded set can't be re-verified."""
        stock, tuned, _ = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        recipe["volatile"]["excluded"] = []
        recipe["volatile"]["summary"]["excluded_count"] = 0

        result = audit(stock, tuned, recipe)
        assert result.fingerprint_ok is False
        assert any("declares no excluded" in w for w in result.warnings)

    def test_volatile_tampered_fingerprint_fails_self_consistency(self) -> None:
        """A stored fingerprint that doesn't match the kept set fails."""
        stock, tuned, _ = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        recipe["fingerprint"] = "sha256:" + "0" * 64

        result = audit(stock, tuned, recipe)
        assert result.fingerprint_ok is False
        assert any("does not match its own" in w for w in result.warnings)

    def test_volatile_extra_hidden_edit_is_unaccounted(self) -> None:
        """An edit OUTSIDE the declared exclusions is still reported as
        unaccounted — verdict 3, not the subset rule, catches tampering
        that the recipe itself never claimed to describe."""
        stock, tuned, _ = _vin_pair()
        recipe = _cook_volatile(stock, tuned)
        tampered = bytearray(tuned)
        tampered[0x900] ^= 0xFF  # hidden edit the recipe knows nothing about

        result = audit(stock, bytes(tampered), recipe)
        # The recipe's own instructions are still an honest subset of the
        # diff (fingerprint holds); the hidden byte is a verdict-3 hit.
        assert result.fingerprint_ok is True
        assert any(b.offset == 0x900 for b in result.unaccounted_blocks)
        assert result.clean is False
