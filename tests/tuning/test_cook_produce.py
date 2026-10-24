"""
Tests for the new producer — ``cook-strict`` / ``cook-forced``.

The invariant that matters most is the **round trip**:

    apply(cook(stock, tuned), stock) == tuned        byte for byte

If that holds, the producer and the consumer agree on what a recipe means. The
rest pins the guards (size, one firmware), the mode difference (unique vs
repeated anchors -> ``identity_floor``), and the package's founding promise:
**nothing is ever excluded**.

Synthetic fixtures come from ``tests/conftest.py``; the real pairs come from the
gitignored corpus and the tests skip when it is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path

import pytest

from openremap.core.services.recipes.apply.contract import read_contract
from openremap.core.services.recipes.apply.engine import apply_strict
from openremap.core.services.recipes.produce.builder import produce_recipe
from openremap.core.services.recipes.produce.contract import (
    build_contract,
    verify_recipe,
)
from openremap.core.services.recipes.produce.modes import (
    COOK_FORCED,
    COOK_STRICT,
    get_mode,
)

ROOT = Path(__file__).parent.parent.parent
GOLDENS = ROOT / "tests" / "tuning" / "parity_goldens.json"
TUNE_DIR = ROOT / "tests" / "data" / "tune"
TUNE_STOCK = TUNE_DIR / "original.bin"
TUNE_TUNED = TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pair(n_changes: int = 3, size: int = 4096) -> tuple[bytes, bytes]:
    """A small stock/tuned pair with *n_changes* single-byte edits.

    The stock is pseudo-random so its context anchors are actually unique —
    an all-zero fixture would make every anchor repeat, which is what
    ``cook-forced`` exists for, not what the default mode should see.
    """
    seed = random.Random(1234)
    stock = bytearray(seed.randbytes(size))
    tuned = bytearray(stock)
    for i in range(n_changes):
        tuned[512 + i * 512] ^= 0xFF
    return bytes(stock), bytes(tuned)


@pytest.fixture
def same_firmware(monkeypatch):
    """Both binaries identify as the same firmware — on the cook AND apply side."""

    def _identify(data, filename="x"):
        return {
            "match_key": "TEST::SAME",
            "manufacturer": "Test",
            "ecu_family": "TEST",
        }

    monkeypatch.setattr(
        "openremap.core.services.recipes.produce.builder.identify_ecu", _identify
    )
    monkeypatch.setattr(
        "openremap.core.services.recipes.apply.engine.identify_ecu", _identify
    )


def _golden_entries() -> list[dict]:
    if not GOLDENS.exists():
        return []
    return json.loads(GOLDENS.read_text(encoding="utf-8"))["entries"]


def _roundtrip(stock: bytes, tuned: bytes, mode) -> dict:
    """Cook, then apply with the first command that accepts the recipe."""
    from openremap.core.services.recipes.apply.engine import apply_unique

    result = produce_recipe(stock, tuned, "stock.bin", "tuned.bin", mode=mode)
    assert result["recipe"] is not None, result["report"].get("refusal")
    expected = hashlib.sha256(tuned).hexdigest()
    for command in (apply_strict, apply_unique):
        applied = command(stock, result["recipe"], "stock.bin", "recipe.remap")
        if applied["applied"]:
            assert applied["patched_sha256"] == expected
            return result["recipe"]
    raise AssertionError(applied["report"].get("refusal"))


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_synthetic_pair(self):
        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        assert recipe["contract"]["identity_floor"] == "any"

    def test_strict_recipe_is_usable_by_every_apply_command(self, same_firmware):
        """With identity verifiable, floor=any means all four commands accept it."""
        from openremap.core.services.recipes.apply.engine import (
            apply_any,
            apply_shift,
            apply_unique,
        )

        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        expected = hashlib.sha256(tuned).hexdigest()
        for command in (apply_strict, apply_shift, apply_unique, apply_any):
            result = command(stock, recipe, "stock.bin", "recipe.remap")
            assert result["applied"] is True, command.__name__
            assert result["patched_sha256"] == expected, command.__name__

    def test_unidentified_pair_is_usable_by_the_identity_free_commands(self):
        """No match_key in the recipe -> only the identity-free commands apply."""
        from openremap.core.services.recipes.apply.engine import (
            apply_any,
            apply_shift,
            apply_unique,
        )

        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        expected = hashlib.sha256(tuned).hexdigest()
        for command in (apply_unique, apply_any):
            result = command(stock, recipe, "stock.bin", "recipe.remap")
            assert result["applied"] is True, command.__name__
            assert result["patched_sha256"] == expected, command.__name__
        for command in (apply_strict, apply_shift):
            result = command(stock, recipe, "stock.bin", "recipe.remap")
            assert result["applied"] is False, command.__name__
            assert result["report"]["refusal"]["check"] == "Γ2", command.__name__


# ---------------------------------------------------------------------------
# The two modes
# ---------------------------------------------------------------------------


class TestModes:
    def test_mode_registry(self):
        assert get_mode("cook-strict") is COOK_STRICT
        assert get_mode("cook-forced") is COOK_FORCED
        with pytest.raises(ValueError):
            get_mode("cook-nonsense")

    def test_forced_mode_declares_firmware_floor(self, same_firmware):
        """An edit inside padding: unique anchors are impossible."""
        size = 1024
        stock = bytes([0xFF] * size)
        patch = bytearray(stock)
        patch[0x200] = 0x00
        recipe = _roundtrip(stock, bytes(patch), COOK_FORCED)
        assert recipe["contract"]["anchors_unique"] is False
        assert recipe["contract"]["identity_floor"] == "firmware"

    def test_forced_refuses_an_unidentified_pair(self):
        """floor=firmware needs a match_key; without one the recipe is dead."""
        stock = bytes([0xFF] * 1024)
        patch = bytearray(stock)
        patch[0x200] = 0x00
        result = produce_recipe(stock, bytes(patch), "s.bin", "t.bin", mode=COOK_FORCED)
        assert result["recipe"] is None
        assert result["report"]["refusal"]["rule"] == "identity_required"

    def test_strict_refuses_the_same_pair(self):
        size = 1024
        stock = bytes([0xFF] * size)
        patch = bytearray(stock)
        patch[0x200] = 0x00
        result = produce_recipe(stock, bytes(patch), "s.bin", "t.bin", mode=COOK_STRICT)
        assert result["recipe"] is None
        assert result["report"]["refusal"]["rule"] == "unique"
        assert "cook-forced" in (result["report"]["next_step"] or "")

    def test_forced_recipe_is_refused_by_identity_free_commands(self, same_firmware):
        size = 1024
        stock = bytes([0xFF] * size)
        patch = bytearray(stock)
        patch[0x200] = 0x00
        recipe = _roundtrip(stock, bytes(patch), COOK_FORCED)
        from openremap.core.services.recipes.apply.engine import apply_any

        result = apply_any(stock, recipe, "stock.bin", "recipe.remap")
        assert result["applied"] is False
        assert result["report"]["refusal"]["check"] == "Γ2"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_identical_files_are_refused_not_a_crash(self):
        """The easiest user mistake: the same file picked twice.

        The analyzer diffs them to nothing and hands back an empty recipe, which
        the apply reader rejects (A12). That used to surface as an engine
        RuntimeError; it must be a refusal with a reason and a next step.
        """
        stock, _ = _pair()
        result = produce_recipe(stock, stock, "s.bin", "s.bin", mode=COOK_STRICT)
        assert result["recipe"] is None
        assert result["report"]["refusal"]["rule"] == "empty"
        assert result["report"]["next_step"]

    def test_every_refusal_message_is_english(self):
        """The CLI and the TUI print these strings verbatim."""
        import re

        greek = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
        from openremap.core.services.recipes.produce import report as report_mod

        for rule, text in report_mod.NEXT_STEP.items():
            assert not greek.search(text), f"{rule}: {text}"

    def test_size_mismatch_refused(self):
        stock, _ = _pair()
        tuned, _ = _pair(size=2048)
        result = produce_recipe(stock, tuned, "s.bin", "t.bin", mode=COOK_STRICT)
        assert result["recipe"] is None
        assert result["report"]["refusal"]["rule"] == "size"

    def test_two_different_firmwares_refused(self, monkeypatch):
        """The guard that makes identity_floor:any meaningful."""
        from openremap.core.services.recipes.produce import builder

        keys = iter(["EDC16::111", "SIMOS::222"])
        monkeypatch.setattr(
            builder, "identify_ecu", lambda data, filename="x": {"match_key": next(keys)}
        )
        stock, tuned = _pair()
        result = produce_recipe(stock, tuned, "s.bin", "t.bin", mode=COOK_STRICT)
        assert result["recipe"] is None
        assert result["report"]["refusal"]["rule"] == "identity"

    def test_unidentified_pair_accepted_with_a_note(self, monkeypatch):
        from openremap.core.services.recipes.produce import builder

        monkeypatch.setattr(
            builder, "identify_ecu", lambda data, filename="x": {"match_key": None}
        )
        stock, tuned = _pair()
        result = produce_recipe(stock, tuned, "s.bin", "t.bin", mode=COOK_STRICT)
        assert result["recipe"] is not None
        assert any("Identity does not verify" in n for n in result["report"]["notes"])
        assert result["recipe"]["source"]["identity"] == {}


# ---------------------------------------------------------------------------
# Nothing is excluded
# ---------------------------------------------------------------------------


class TestNoExclusions:
    def test_every_changed_block_becomes_an_instruction(self):
        stock, tuned = _pair(n_changes=4)
        result = produce_recipe(stock, tuned, "s.bin", "t.bin", mode=COOK_STRICT)
        recipe = result["recipe"]
        assert len(recipe["instructions"]) == 4
        # No volatile/exclusion machinery anywhere in the produced document.
        assert "volatile" not in recipe
        assert not any("excluded" in key for key in recipe)
        assert result["report"]["mode"]["exclusions"].startswith("none")

    def test_no_checksum_data_in_the_recipe(self):
        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        text = json.dumps(recipe)
        assert "checksum" not in text.lower()
        assert "checksums_touched" not in text


# ---------------------------------------------------------------------------
# Contract writer / reader agreement
# ---------------------------------------------------------------------------


class TestContract:
    def test_writer_output_is_accepted_by_the_reader(self):
        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        contract = read_contract(recipe)  # must not raise
        assert contract.identity_floor == recipe["contract"]["identity_floor"]
        assert contract.sections == tuple(recipe["contract"]["sections"])

    def test_sections_reflect_present_sections(self):
        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        assert recipe["contract"]["sections"] == sorted(
            s for s in ("maps", "regions") if s in recipe
        )

    def test_tampered_recipe_fails_verification(self):
        stock, tuned = _pair()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        recipe["contract"]["anchors_unique"] = not recipe["contract"]["anchors_unique"]
        with pytest.raises(RuntimeError):
            verify_recipe(recipe)

    def test_build_contract_follows_uniqueness(self):
        unique = build_contract([{"ctx_unique": True}], {})
        repeated = build_contract([{"ctx_unique": False}], {})
        assert unique["identity_floor"] == "any"
        assert repeated["identity_floor"] == "firmware"


# ---------------------------------------------------------------------------
# Real corpus (gitignored)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not GOLDENS.exists(), reason="parity goldens absent")
class TestRealPairs:
    def test_real_pair_round_trip(self):
        if not TUNE_STOCK.exists():
            pytest.skip("tests/data/tune corpus absent")
        stock = TUNE_STOCK.read_bytes()
        tuned = TUNE_TUNED.read_bytes()
        recipe = _roundtrip(stock, tuned, COOK_STRICT)
        assert len(recipe["instructions"]) == 79
        assert recipe["contract"]["identity_floor"] == "any"
        assert recipe["regions"]["tagged"] == 79

    def test_golden_pairs(self):
        """Every recorded pair cooks and applies back to the tuned file."""
        entries = [e for e in _golden_entries()][: int(os.environ.get("COOK_SAMPLE", "10"))]
        checked = 0
        for entry in entries:
            stock_path = ROOT / entry["stock"]
            tuned_path = ROOT / entry["tuned"]
            if not stock_path.exists() or not tuned_path.exists():
                continue
            mode = COOK_STRICT if entry["require_unique"] else COOK_FORCED
            _roundtrip(stock_path.read_bytes(), tuned_path.read_bytes(), mode)
            checked += 1
        if checked == 0:
            pytest.skip("corpus absent")
        assert checked >= min(8, len(entries))
