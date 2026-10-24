"""
Tests for the new apply engine — ``tune-strict`` and ``tune-shift``.

Both speak **schema 5.0 only**; legacy 4.x recipes are refused with a pointer to
the deprecated ``openremap tune``. That is asserted here too, because it is a
deliberate product decision, not an omission.

The four commands relax, in order: **identity** (match_key), then **position**
(exact offset), then **uniqueness** (one anchor candidate):

  tune-strict   identity + exact offset + shift 0
  tune-shift    identity + anchor ±2048, ambiguity refused
  tune-unique   no identity + exact offset + shift 0, ambiguity refused
  tune-any      no identity + anchor ±2048, ambiguity reported

``TestShiftMode``, ``TestUniqueMode`` and ``TestAnyMode`` pin those differences.

Covered in the order the engine runs them:

* recipe validity (A1-A5, A12) and the 5.0-only rule
* identity floor + identity gate (Γ1-Γ3)
* exact bytes / already patched (Γ4, Γ5)
* anchor search and shift (Γ6, Γ7)
* informational-only checks that must never block (A7-A9, Γ8, Γ9)
* the checksum disclosure: reported on the *written file*, never a refusal
* post-verification and accounting (Δ1, Δ4)

Unit tests monkeypatch the engine's single ``identify_ecu`` call so they stay
fast and offline. The last class runs the real binary pair from
``tests/data/tune/`` (skipped when the corpus is absent): the cooked 4.4 recipe
is converted to 5.0 in the test so the engine can run it, and the patched bytes
are asserted equal to the deprecated ``tune_binary``'s output.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from openremap.core.services.recipes.apply import engine as engine_mod
from openremap.core.services.recipes.apply.contract import read_contract
from openremap.core.services.recipes.apply.engine import (
    apply_any,
    apply_shift,
    apply_strict,
    apply_unique,
)
from openremap.core.services.recipes.produce.builder import produce_recipe
from openremap.core.services.recipes.produce.modes import COOK_FORCED, COOK_STRICT
from tests.conftest import make_bin_with, make_instruction, make_recipe

ROOT = Path(__file__).parent.parent.parent
TUNE_DIR = ROOT / "tests" / "data" / "tune"
TUNE_STOCK = TUNE_DIR / "original.bin"
TUNE_TUNED = TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"

MATCH_KEY = "TESTECU::1234567890"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _target(size: int = 256, offset: int = 64, ob: bytes = b"\x11\x22\x33\x44") -> bytes:
    """A small binary with *ob* planted at *offset*."""
    return make_bin_with(size, {offset: ob})


def _instruction(
    target: bytes, offset: int, ob: bytes, mb: bytes, ctx_len: int = 16
) -> dict:
    ctx = target[max(0, offset - ctx_len):offset].hex().upper()
    inst = make_instruction(offset, ob.hex().upper(), mb.hex().upper(), ctx=ctx)
    inst["context_after"] = target[
        offset + len(ob):offset + len(ob) + ctx_len
    ].hex().upper()
    inst["ctx_unique"] = True
    return inst


def _recipe(
    *,
    size: int = 256,
    offset: int = 64,
    ob: bytes = b"\x11\x22\x33\x44",
    mb: bytes = b"\xAA\xBB\xCC\xDD",
    ctx_len: int = 16,
    contract: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """A one-instruction, schema-5.0 recipe that applies cleanly to ``_target``."""
    target = _target(size, offset, ob)
    inst = _instruction(target, offset, ob, mb, ctx_len)
    declared = {
        "identity_floor": "any",
        "anchors_unique": True,
        "sections": [],
    }
    declared.update(contract or {})
    recipe = {
        "type": "recipe",
        "schema_version": "5.0",
        "contract": declared,
        "producer": {
            "engine": "openremap",
            "version": "0.8.0",
            "command": "cook",
            "created_at": "2026-10-19T00:00:00Z",
            "warnings": [],
        },
        "source": {
            "filename": "stock.bin",
            "size": size,
            "sha256": hashlib.sha256(target).hexdigest(),
            "identity": {"match_key": MATCH_KEY, "manufacturer": "Test"},
        },
        "tune": {"filename": "tuned.bin", "size": size, "sha256": "0" * 64},
        "fingerprint": "sha256:" + "0" * 64,
        "metadata": {"name": "test"},
        "statistics": {},
        "instructions": [inst],
    }
    recipe.update(extra or {})
    return recipe


def _legacy_recipe() -> dict:
    """A schema-4.4 document — what the deleted engine used to produce.

    Nothing emits 4.x any more, so the fixture is a literal: the point of the
    test is that a legacy document is refused with a pointer to the current
    commands, not that we can still build one.
    """
    return {
        "type": "recipe",
        "schema_version": "4.4",
        "source": "full_cook",
        "application": "openremap-core",
        "metadata": {},
        "ecu": {"match_key": MATCH_KEY, "file_size": 256},
        "statistics": {},
        "instructions": [],
    }


@pytest.fixture
def identified(monkeypatch):
    """Make the engine's single identify call return *MATCH_KEY*."""

    def _identify(data: bytes, filename: str = "x.bin") -> dict:
        return {
            "match_key": MATCH_KEY,
            "manufacturer": "Test",
            "ecu_family": "TESTECU",
            "confidence": "HIGH",
        }

    monkeypatch.setattr(engine_mod, "identify_ecu", _identify)
    return _identify


def _refusal(result: dict) -> str:
    return (result["report"].get("refusal") or {}).get("check")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestApplies:
    def test_clean_apply(self, identified):
        result = apply_strict(_target(), _recipe(), "target.bin", "r.remap")
        assert result["applied"] is True
        patched = base64.b64decode(result["patched_b64"])
        assert patched[64:68] == b"\xAA\xBB\xCC\xDD"
        assert result["patched_sha256"] == hashlib.sha256(patched).hexdigest()
        verdicts = {c["id"]: c["verdict"] for c in result["report"]["checks"]}
        for rule in ("Γ1", "Γ2", "Γ4", "Γ6", "Γ7", "Δ1", "Δ4"):
            assert verdicts[rule] == "pass", rule

    def test_writes_nothing_when_refused(self, identified):
        result = apply_strict(
            _target(ob=b"\x00\x00\x00\x00"), _recipe(), "t.bin", "r.remap"
        )
        assert result["applied"] is False
        assert result["patched_b64"] is None
        assert result["patched_sha256"] is None

    def test_report_is_json_safe(self, identified):
        json.dumps(apply_strict(_target(), _recipe(), "t.bin", "r.remap")["report"])

    def test_a_successful_apply_discloses_the_checksum_position(self, identified):
        """'APPLIED' is a claim about the recipe, not about the file.

        Nothing in this engine rewrites a stored checksum, so a patched file can
        verify byte-for-byte against what the recipe promised and still be
        unflashable. The report carries the measured answer — ``skip`` here,
        because a 256-byte synthetic binary satisfies no scheme we know.
        """
        result = apply_strict(_target(), _recipe(), "t.bin", "r.remap")
        checksums = result["report"]["result"]["checksums"]
        assert checksums["corrected"] is False
        assert checksums["status"] == "skip"
        assert "checksum" in checksums["check_with"]

    def test_a_successful_apply_carries_the_disclaimer(self, identified):
        """Three claims the tool cannot make for the operator, on every success."""
        disclaimer = apply_strict(_target(), _recipe(), "t.bin", "r.remap")["report"][
            "result"
        ]["disclaimer"]
        assert "does not correct checksums automatically" in disclaimer
        assert "dedicated tuner shop" in disclaimer
        assert "right to repair and track racing" in disclaimer
        assert "everyday car" in disclaimer

    def test_a_refusal_carries_no_checksum_claim(self, identified):
        """Nothing was written, so there is nothing to warn about."""
        result = apply_strict(
            _target(ob=b"\x00\x00\x00\x00"), _recipe(), "t.bin", "r.remap"
        )
        assert result["applied"] is False
        assert "checksums" not in result["report"]["result"]

    def test_the_checksum_verdict_is_measured_not_assumed(self, identified, monkeypatch):
        """A stale scheme must be reported as ``fail``, not papered over.

        The engine asks the checksum service about the bytes it produced; this
        pins that the answer is passed through instead of being hard-coded.
        """
        from openremap.core.services.health import HealthCheck
        from openremap.core.services.recipes.apply import report as report_mod

        monkeypatch.setattr(
            "openremap.core.services.health.check_checksums",
            lambda data: HealthCheck("checksums", "fail", "ME7 main", ["stale"]),
        )
        disclosure = report_mod.checksum_disclosure(b"\x00" * 16)
        assert disclosure["status"] == "fail"
        assert disclosure["message"] == "ME7 main"
        assert disclosure["corrected"] is False

    def test_other_car_same_firmware_applies(self, identified):
        """A byte outside every anchor differs — same firmware, still applies."""
        target = bytearray(_target())
        target[0] = 0x5A if target[0] != 0x5A else 0x5B
        result = apply_strict(bytes(target), _recipe(), "car2.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["identity"]["same_file"] is False


# ---------------------------------------------------------------------------
# Recipe validity (A1-A5, A12) and the 5.0-only rule
# ---------------------------------------------------------------------------


class TestRecipeValidity:
    def test_not_a_recipe(self):
        recipe = _recipe()
        recipe["type"] = "identification"
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A1"

    def test_missing_schema(self):
        recipe = _recipe()
        recipe.pop("schema_version")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A2"

    def test_schema_too_old(self):
        recipe = _recipe()
        recipe["schema_version"] = "4.2"
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A2"

    def test_legacy_4x_refused_with_what_to_do_instead(self):
        """The 4.x tool is gone, so the refusal must not point at it."""
        result = apply_strict(_target(), _legacy_recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "A2"
        assert "5.0 only" in result["report"]["refusal"]["message"]
        assert "cook" in result["report"]["refusal"]["message"]
        assert "cook" in (result["report"]["next_step"] or "")

    def test_future_major_refused(self):
        recipe = _recipe()
        recipe["schema_version"] = "6.0"
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A3"

    def test_unknown_top_level_section(self):
        recipe = _recipe(extra={"telemetry": {"x": 1}})
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A4"

    def test_legacy_ecu_block_is_unknown_in_5_0(self, identified):
        recipe = _recipe()
        recipe["ecu"] = {"match_key": MATCH_KEY}
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A4"

    def test_unknown_instruction_field(self):
        recipe = _recipe()
        recipe["instructions"][0]["mask"] = "FF00"
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A4"

    def test_missing_semantic_field(self):
        recipe = _recipe()
        recipe["instructions"][0].pop("ctx")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A12"

    def test_missing_ctx_unique(self):
        recipe = _recipe()
        recipe["instructions"][0].pop("ctx_unique")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A12"

    def test_no_instructions(self):
        recipe = _recipe()
        recipe["instructions"] = []
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A12"

    def test_bad_hex(self):
        recipe = _recipe()
        recipe["instructions"][0]["ob"] = "ZZ"
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A12"


# ---------------------------------------------------------------------------
# Contract self-verification (A5)
# ---------------------------------------------------------------------------


class TestContractVerification:
    def test_tampered_anchors_unique_refused(self):
        recipe = _recipe(contract={"anchors_unique": False})
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A5"

    def test_tampered_sections_refused(self):
        recipe = _recipe(contract={"sections": ["maps"]})
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A5"

    def test_non_unique_anchor_with_any_floor_refused(self):
        """A recipe whose anchors are not unique cannot claim anchors_unique."""
        recipe = _recipe()
        recipe["instructions"][0]["ctx_unique"] = False
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A5"

    def test_unknown_contract_field_refused(self):
        recipe = _recipe()
        recipe["contract"]["telemetry"] = True
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A4"

    def test_missing_contract_refused(self):
        recipe = _recipe()
        recipe.pop("contract")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "A5"

    def test_consistent_contract_accepted(self):
        recipe = _recipe(contract={"identity_floor": "firmware"})
        contract = read_contract(recipe)
        assert contract.anchors_unique is True
        assert contract.identity_floor == "firmware"
        assert contract.sections == ()

    def test_recipe_floor_stricter_than_command_refused(self, identified):
        """A recipe demanding file identity is not applied at firmware level."""
        recipe = _recipe(contract={"identity_floor": "file"})
        result = apply_strict(_target(), recipe, "t.bin", "r.remap")
        assert _refusal(result) == "Γ2"
        assert "'file'" in result["report"]["refusal"]["message"]


# ---------------------------------------------------------------------------
# Identity (Γ1-Γ3)
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_no_source_size(self, identified):
        recipe = _recipe()
        recipe["source"].pop("size")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "Γ1"

    def test_size_mismatch(self, identified):
        recipe = _recipe()
        recipe["source"]["size"] = 999
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "Γ1"

    def test_recipe_without_match_key(self, identified):
        recipe = _recipe()
        recipe["source"]["identity"].pop("match_key")
        assert _refusal(apply_strict(_target(), recipe, "t.bin", "r.remap")) == "Γ2"

    def test_target_not_identified(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": None}
        )
        result = apply_strict(_target(), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ2"
        assert "not recognised" in result["report"]["refusal"]["message"]

    def test_different_firmware_refused_with_next_step(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod,
            "identify_ecu",
            lambda data, filename="x": {"match_key": "OTHERECU::999"},
        )
        result = apply_strict(_target(), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ2"
        assert result["report"]["next_step"]


# ---------------------------------------------------------------------------
# Exact bytes, anchors, shift (Γ4-Γ7)
# ---------------------------------------------------------------------------


class TestBytesAndAnchors:
    def test_wrong_original_bytes(self, identified):
        target = make_bin_with(256, {64: b"\xDE\xAD\xBE\xEF"})
        assert _refusal(apply_strict(target, _recipe(), "t.bin", "r.remap")) == "Γ4"

    def test_already_patched(self, identified):
        patched = make_bin_with(256, {64: b"\xAA\xBB\xCC\xDD"})
        result = apply_strict(patched, _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ5"
        assert "already carries this recipe" in result["report"]["refusal"]["message"]

    def test_anchor_not_found(self, identified):
        """ob is at its offset (Γ4 passes) but the context differs (Γ6)."""
        target = bytearray(_target())
        target[64 - 16:64] = bytes(range(16))
        result = apply_strict(bytes(target), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ6"

    def test_shift_refused(self, identified, monkeypatch):
        import openremap.core.services.recipes.patcher as patcher_mod

        real_apply = patcher_mod.ECUPatcher.apply_all

        def _shifted(self):
            data = real_apply(self)
            for res in self.results:
                res.shift = 4
            return data

        monkeypatch.setattr(patcher_mod.ECUPatcher, "apply_all", _shifted)
        result = apply_strict(_target(), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ7"


# ---------------------------------------------------------------------------
# Informational checks never block
# ---------------------------------------------------------------------------


class TestInformationOnly:
    def test_ambiguous_anchor_is_reported_not_blocking(self, identified):
        """Identical look-alikes: reported by Γ8, still applied."""
        size, offset = 1024, 512
        target = bytearray(b"\xFF" * size)
        for pos in (100, 300, offset, 700):
            target[pos] = 0x11
        target = bytes(target)
        inst = _instruction(target, offset, b"\x11", b"\x22")
        inst["ctx_unique"] = False
        recipe = _recipe(
            size=size,
            offset=offset,
            ob=b"\x11",
            mb=b"\x22",
            contract={"anchors_unique": False, "identity_floor": "firmware"},
        )
        recipe["instructions"] = [inst]
        recipe["source"]["sha256"] = hashlib.sha256(target).hexdigest()
        result = apply_strict(target, recipe, "t.bin", "r.remap")
        assert result["applied"] is True
        g8 = {c["id"]: c for c in result["report"]["checks"]}["Γ8"]
        assert "max" in g8["detail"]
        assert result["report"]["anchors"]["max_hits"] > 1

    def test_regions_reported(self, identified):
        recipe = _recipe(
            extra={
                "regions": {"tagged": 1, "risky": 0, "by_region": {"calibration": 1}}
            },
            contract={"sections": ["regions"]},
        )
        result = apply_strict(_target(), recipe, "t.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["regions"]["tagged"] == 1


# ---------------------------------------------------------------------------
# Real corpus (gitignored) — end-to-end + parity with the old engine
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not TUNE_STOCK.exists(), reason="tests/data/tune corpus absent")
class TestRealPair:
    @pytest.fixture(scope="class")
    def pair(self):
        
        stock = TUNE_STOCK.read_bytes()
        tuned = TUNE_TUNED.read_bytes()
        cooked = produce_recipe(
            stock, tuned, TUNE_STOCK.name, TUNE_TUNED.name, mode=COOK_STRICT
        )["recipe"]
        return stock, tuned, cooked

    def test_legacy_document_is_refused(self, pair):
        """Nothing emits 4.x now, but a stored document must still be refused."""
        stock, _tuned, _cooked = pair
        result = apply_strict(stock, _legacy_recipe(), "stock.bin", "legacy.remap")
        assert _refusal(result) == "A2"
        assert "schema 4.4" in result["report"]["refusal"]["message"].lower() or (
            "4.4" in result["report"]["refusal"]["message"]
        )
        assert "openremap tune" in (result["report"]["next_step"] or "") or (
            "5.0" in (result["report"]["next_step"] or "")
        )

    def test_applies_to_its_own_stock_as_5_0(self, pair):
        stock, _tuned, cooked = pair
        result = apply_strict(stock, cooked, "stock.bin", "tune.remap")
        assert result["applied"] is True
        assert result["report"]["anchors"]["max_hits"] == 1

    def test_already_patched_refused(self, pair):
        _stock, tuned, cooked = pair
        result = apply_strict(tuned, cooked, "tuned.bin", "tune.remap")
        assert _refusal(result) == "Γ5"

    def test_other_car_applies(self, pair):
        stock, _tuned, cooked = pair
        other = bytearray(stock)
        other[0x100] = 0x5A if other[0x100] != 0x5A else 0x5B
        result = apply_strict(bytes(other), cooked, "car2.bin", "tune.remap")
        assert result["applied"] is True
        assert result["report"]["identity"]["same_file"] is False

    def test_different_ecu_refused(self, pair):
        """Another family at the same size must be refused on identity.

        Uses a same-size cross-family pair (EDC16 vs SIMOS 2441, both 262 144 B)
        and a real identification on both sides — no monkeypatching.
        """
        edc16 = ROOT / "tests/data/ECUs/Bosch/EDC16/016FEorg.127__1__1.bin"
        simos_dir = ROOT / "tests/data/ECUs/Siemens/SIMOS 2441"
        if not edc16.exists() or not simos_dir.exists():
            pytest.skip("cross-family pair absent")
        simos = [p for p in simos_dir.iterdir() if p.is_file()]
        if not simos or simos[0].stat().st_size != edc16.stat().st_size:
            pytest.skip("no same-size cross-family binary available")

        
        stock = edc16.read_bytes()
        patched = bytearray(stock)
        patched[0x30000] = 0x00 if stock[0x30000] != 0x00 else 0x01
        cooked = produce_recipe(
            stock, bytes(patched), edc16.name, "blank_edit.bin", mode=COOK_FORCED
        )["recipe"]
        assert cooked is not None

        result = apply_strict(simos[0].read_bytes(), cooked, simos[0].name, "r.remap")
        assert _refusal(result) == "Γ2"
        assert "Different firmware" in result["report"]["refusal"]["message"]


# ---------------------------------------------------------------------------
# tune-shift — one rung looser: displaced anchor allowed, ambiguity refused
# ---------------------------------------------------------------------------


def _shifted_target(stock: bytes, by: int = 8) -> bytes:
    """The same content, moved *by* bytes later — a layout that drifted."""
    return bytes(by) + stock[: len(stock) - by]


class TestShiftMode:
    def test_strict_refuses_a_displaced_anchor(self, identified):
        stock = _target()
        recipe = _recipe()
        result = apply_strict(_shifted_target(stock), recipe, "t.bin", "r.remap")
        assert _refusal(result) == "Γ4"

    def test_shift_mode_applies_and_reports_the_shift(self, identified):
        stock = _target()
        recipe = _recipe()
        target = _shifted_target(stock)
        result = apply_shift(target, recipe, "t.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["anchors"]["shifted"] == 1
        assert result["report"]["anchors"]["max_shift"] == 8
        patched = base64.b64decode(result["patched_b64"])
        assert patched[64 + 8:64 + 8 + 4] == b"\xAA\xBB\xCC\xDD"
        verdicts = {c["id"]: c["verdict"] for c in result["report"]["checks"]}
        assert verdicts["Γ6"] == "pass"
        assert verdicts["Γ8"] == "pass"
        assert verdicts["Δ1"] == "pass"
        assert verdicts["Δ4"] == "pass"

    def test_shift_mode_still_requires_the_same_firmware(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": "OTHER::1"}
        )
        result = apply_shift(_shifted_target(_target()), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ2"

    def test_shift_mode_still_refuses_legacy_recipes(self):
        result = apply_shift(_target(), _legacy_recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "A2"

    def test_shift_mode_refuses_ambiguous_anchor(self, identified):
        """A look-alike anchor is a refusal once shift is allowed."""
        size, offset = 1024, 512
        target = bytearray(b"\xFF" * size)
        for pos in (100, 300, offset, 700):
            target[pos] = 0x11
        target = bytes(target)
        inst = _instruction(target, offset, b"\x11", b"\x22")
        inst["ctx_unique"] = False
        recipe = _recipe(
            size=size,
            offset=offset,
            ob=b"\x11",
            mb=b"\x22",
            contract={"anchors_unique": False, "identity_floor": "firmware"},
        )
        recipe["instructions"] = [inst]
        recipe["source"]["sha256"] = hashlib.sha256(target).hexdigest()
        result = apply_shift(target, recipe, "t.bin", "r.remap")
        assert _refusal(result) == "Γ8"

    def test_shift_mode_detects_already_patched(self, identified):
        patched = make_bin_with(256, {64: b"\xAA\xBB\xCC\xDD"})
        result = apply_shift(patched, _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ5"
        assert "already carries this recipe" in result["report"]["refusal"]["message"]

    def test_shift_mode_reports_policy(self, identified):
        result = apply_shift(_target(), _recipe(), "t.bin", "r.remap")
        policy = result["report"]["policy"]
        assert policy["shift"] == "allowed, reported"
        assert policy["ambiguity"] == "must be unique"
        assert result["report"]["command"] == "tune-shift"


# ---------------------------------------------------------------------------
# tune-unique — no identity, but exact position and a unique anchor
# ---------------------------------------------------------------------------


class TestUniqueMode:
    def test_applies_without_identity(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": None}
        )
        result = apply_unique(_target(), _recipe(), "t.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["command"] == "tune-unique"

    def test_still_requires_exact_offsets(self, identified):
        """A displaced anchor is refused: this rung keeps the position rail."""
        result = apply_unique(_shifted_target(_target()), _recipe(), "t.bin", "r.remap")
        assert _refusal(result) == "Γ4"

    def test_refuses_ambiguous_anchor(self, identified):
        size, offset = 1024, 512
        target = bytearray(b"\xFF" * size)
        for pos in (100, 300, offset, 700):
            target[pos] = 0x11
        target = bytes(target)
        inst = _instruction(target, offset, b"\x11", b"\x22")
        inst["ctx_unique"] = False
        recipe = _recipe(
            size=size,
            offset=offset,
            ob=b"\x11",
            mb=b"\x22",
            contract={"anchors_unique": False, "identity_floor": "firmware"},
        )
        recipe["instructions"] = [inst]
        recipe["source"]["sha256"] = hashlib.sha256(target).hexdigest()
        # A recipe with non-unique anchors cannot declare floor 'any' (A5),
        # and with floor 'firmware' an identity-free command cannot apply it.
        assert _refusal(apply_unique(target, recipe, "t.bin", "r.remap")) == "Γ2"

    def test_ambiguous_anchor_in_target_is_reported(self, identified):
        """Anchor unique in the stock, several copies in the target.

        With an exact position the patcher's "closest match" is the expected
        offset by construction, so this is information, not a refusal — the same
        rule tune-strict follows.
        """
        # Unique anchor in the recipe's STOCK, several copies in the TARGET.
        size, offset = 1024, 512
        stock = bytearray(b"\xFF" * size)
        stock[offset] = 0x11
        stock = bytes(stock)
        target = bytearray(stock)
        for pos in (100, 300, 700):
            target[pos] = 0x11
        target = bytes(target)
        inst = _instruction(stock, offset, b"\x11", b"\x22")
        recipe = _recipe(size=size, offset=offset, ob=b"\x11", mb=b"\x22")
        recipe["instructions"] = [inst]
        recipe["source"]["sha256"] = hashlib.sha256(stock).hexdigest()

        result = apply_unique(target, recipe, "t.bin", "r.remap")
        assert result["applied"] is True
        g8 = {c["id"]: c for c in result["report"]["checks"]}["Γ8"]
        assert g8["verdict"] == "info"
        assert result["report"]["anchors"]["multi_hit"] >= 1

    def test_refuses_recipe_that_demands_identity(self, identified):
        """The contract floor still binds: an 'any' command cannot apply it."""
        recipe = _recipe(contract={"identity_floor": "firmware"})
        result = apply_unique(_target(), recipe, "t.bin", "r.remap")
        assert _refusal(result) == "Γ2"
        assert "'firmware'" in result["report"]["refusal"]["message"]

    def test_reports_identity_mismatch_as_information(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": "OTHER::1"}
        )
        result = apply_unique(_target(), _recipe(), "t.bin", "r.remap")
        assert result["applied"] is True
        g2 = {c["id"]: c for c in result["report"]["checks"]}["Γ2"]
        assert g2["verdict"] == "info"
        assert "not checked" in g2["detail"]


# ---------------------------------------------------------------------------
# tune-any — no identity, shift allowed, ambiguity reported
# ---------------------------------------------------------------------------


class TestAnyMode:
    def test_applies_to_a_displaced_layout_without_identity(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": None}
        )
        result = apply_any(_shifted_target(_target()), _recipe(), "t.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["command"] == "tune-any"
        assert result["report"]["anchors"]["max_shift"] == 8

    def test_ambiguous_anchor_is_reported_not_refused(self, identified):
        """Same fixture: tune-any writes, and reports the look-alikes."""
        # Unique anchor in the recipe's STOCK, several copies in the TARGET.
        size, offset = 1024, 512
        stock = bytearray(b"\xFF" * size)
        stock[offset] = 0x11
        stock = bytes(stock)
        target = bytearray(stock)
        for pos in (100, 300, 700):
            target[pos] = 0x11
        target = bytes(target)
        inst = _instruction(stock, offset, b"\x11", b"\x22")
        recipe = _recipe(size=size, offset=offset, ob=b"\x11", mb=b"\x22")
        recipe["instructions"] = [inst]
        recipe["source"]["sha256"] = hashlib.sha256(stock).hexdigest()
        result = apply_any(target, recipe, "t.bin", "r.remap")
        assert result["applied"] is True
        assert result["report"]["anchors"]["multi_hit"] >= 1

    def test_still_enforces_size_and_verify(self, identified):
        recipe = _recipe()
        recipe["source"]["size"] = 999
        assert _refusal(apply_any(_target(), recipe, "t.bin", "r.remap")) == "Γ1"

    def test_still_refuses_recipe_that_demands_identity(self, identified):
        recipe = _recipe(contract={"identity_floor": "firmware"})
        assert _refusal(apply_any(_target(), recipe, "t.bin", "r.remap")) == "Γ2"

    def test_still_refuses_legacy_recipes(self):
        assert _refusal(apply_any(_target(), _legacy_recipe(), "t.bin", "r.remap")) == "A2"

    def test_reports_different_firmware_loudly(self, monkeypatch):
        monkeypatch.setattr(
            engine_mod, "identify_ecu", lambda data, filename="x": {"match_key": "OTHER::1"}
        )
        result = apply_any(_target(), _recipe(), "t.bin", "r.remap")
        assert result["applied"] is True
        g2 = {c["id"]: c for c in result["report"]["checks"]}["Γ2"]
        assert "DIFFERENT" in g2["detail"]

    def test_policy_block_is_explicit(self, identified):
        policy = apply_any(_target(), _recipe(), "t.bin", "r.remap")["report"]["policy"]
        assert policy["identity"] == "any"
        assert policy["ambiguity"] == "reported"


# ---------------------------------------------------------------------------
# Recorded parity goldens — the oracle that survives deleting the old engine
# ---------------------------------------------------------------------------

GOLDENS = Path(__file__).parent / "parity_goldens.json"


def _golden_entries() -> list[dict]:
    if not GOLDENS.exists():
        return []
    return json.loads(GOLDENS.read_text(encoding="utf-8"))["entries"]


def _selected_entries(entries: list[dict]) -> list[dict]:
    """A deterministic slice, so the default suite stays fast.

    Every entry (real pairs included) is verified when ``OPENREMAP_PARITY_ALL=1``;
    otherwise the real pair plus an evenly spread sample of nine.
    """
    real = [e for e in entries if e["label"].startswith("real/")]
    rest = [e for e in entries if not e["label"].startswith("real/")]
    if os.environ.get("OPENREMAP_PARITY_ALL") == "1" or not rest:
        return real + rest
    step = max(1, len(rest) // 9)
    return real + rest[::step][:9]


class TestRecordedParity:
    """The new engine must reproduce what the deprecated engine produced.

    ``tests/tuning/parity_goldens.json`` holds the old engine's patched
    ``sha256`` for real stock/tuned pairs (hashes only — no firmware bytes). The
    old engine itself is therefore no longer needed at test time, which is what
    makes deleting it safe.
    """

    def test_reproduces_the_recorded_patch(self):
        entries = _selected_entries(_golden_entries())
        if not entries:
            pytest.skip("parity goldens not generated")

        mismatches: list[str] = []
        verified = 0
        available = 0
        refused: list[str] = []

        for entry in entries:
            stock_path = ROOT / entry["stock"]
            tuned_path = ROOT / entry["tuned"]
            if not stock_path.exists() or not tuned_path.exists():
                refused.append(f"{entry['label']} (files absent)")
                continue
            available += 1
            stock = stock_path.read_bytes()
            tuned = tuned_path.read_bytes()
            assert hashlib.sha256(stock).hexdigest() == entry["stock_sha256"]

            mode = COOK_STRICT if entry["require_unique"] else COOK_FORCED
            produced = produce_recipe(
                stock, tuned, stock_path.name, tuned_path.name, mode=mode
            )
            cooked = produced["recipe"]
            assert cooked is not None, produced["report"].get("refusal")
            recipe = (
                cooked
                if str(cooked.get("schema_version", "")).startswith("5.")
                else cooked
            )

            applied = None
            for command in (apply_strict, apply_unique, apply_any):
                result = command(stock, recipe, stock_path.name, "parity.remap")
                if result["applied"]:
                    applied = result
                    break
            if applied is None:
                refused.append(f"{entry['label']} (policy)")
                continue

            verified += 1
            if applied["patched_sha256"] != entry["patched_sha256"]:
                mismatches.append(
                    f"{entry['label']}: got {applied['patched_sha256'][:16]}… "
                    f"expected {entry['patched_sha256'][:16]}…"
                )

        if available == 0:
            pytest.skip("parity corpus absent (tests/data is gitignored)")

        assert not mismatches, "διαφέρουν από τον παλιό engine:\n" + "\n".join(mismatches)
        # Guard against the test quietly degenerating into a no-op.
        assert verified >= min(8, available), (
            f"only {verified}/{available} entries could be applied "
            f"(refused: {refused[:5]}…)"
        )
