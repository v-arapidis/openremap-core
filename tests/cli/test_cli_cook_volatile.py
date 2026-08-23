"""
Tests for ``openremap cook-volatile <original> <modified> [--output recipe.remap]`.

Runs every scenario through the real CLI via typer.testing.CliRunner.
No mocking — all files are created in pytest's tmp_path fixture.

The portability proof (TestPortabilityProof) is the Phase-2 product test:
a recipe cooked from (stockA, tunedA) with a rewritten VIN must apply
cleanly to stockB (== stockA with a DIFFERENT VIN in flash), while the
plain ``cook`` recipe hard-fails on the same stockB.

Fixture shape (seeded random + one ident block + one mirror):
  - the stock binary is random bytes → unique context anchors, so the
    strict Guard-3 default passes without --allow-non-unique;
  - the VIN sits inside a varied-lowercase printable-ASCII run (ident
    block) and is mirrored once → VINScanner scores it >= 0.9
    (WMI + check digit + year + tail + ident-block + mirror), so the
    volatile classifier excludes it;
  - the tuner rewrites the VIN serial AND flips calibration bytes.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from typer.testing import CliRunner

from openremap.cli.main import app
from openremap.core.services.recipes.recipe_builder import check_schema_version

runner = CliRunner()

# Real-shaped VIN: known VW WMI, position-9 check digit '3' (verified in
# tests/tuning/services/test_volatile.py), 'X' model year, numeric tail.
VIN_STOCK = "WVWZZZ1J3XW123456"
VIN_TUNED = "WVWZZZ1J3XW654321"  # tuner rewrote the serial
VIN_OTHER = "WVWZZZ1J3XWABCDEF"  # a DIFFERENT car (stockB)

# Varied lowercase printable padding — keeps the [A-Z0-9]{17} VIN window
# clean (no uppercase/digits in padding) while making context anchors
# unique.  The two pads differ so the primary and mirror records have
# distinct surrounding context.
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

_SIZE = 8192
_VIN_OFF = 0x400
_MIRROR_OFF = 0x600
_CALIB_OFF = 0x1800


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _serial_off(vin_off: int = _VIN_OFF) -> int:
    """Offset of the VIN serial (positions 12-17, 0-based 11..16)."""
    return vin_off + len(_PAD_A1) + 11


def _build_stock(vin: str = VIN_STOCK, seed: int = 7) -> tuple[bytes, int, int]:
    """Random-filled binary (unique anchors) with an ident-block VIN +
    mirror and a calibration byte.  Returns (data, serial_off, calib_off)."""
    rng = random.Random(seed)
    buf = bytearray(rng.randbytes(_SIZE))

    block = _PAD_A1 + vin.encode("ascii") + _PAD_A2
    buf[_VIN_OFF : _VIN_OFF + len(block)] = block
    mirror = _PAD_B1 + vin.encode("ascii") + _PAD_B2
    buf[_MIRROR_OFF : _MIRROR_OFF + len(mirror)] = mirror

    return bytes(buf), _serial_off(), _CALIB_OFF


def _build_tuned(stock: bytes, vin: str = VIN_TUNED) -> bytes:
    """tunedA: rewrite the VIN serial and flip the calibration byte."""
    buf = bytearray(stock)
    so = _serial_off()
    buf[so : so + 6] = vin[11:17].encode("ascii")
    buf[_CALIB_OFF] ^= 0xFF
    return bytes(buf)


def _build_stock_b(stock: bytes) -> bytes:
    """stockB: same SW revision, DIFFERENT VIN serial, stock calibration."""
    buf = bytearray(stock)
    so = _serial_off()
    buf[so : so + 6] = VIN_OTHER[11:17].encode("ascii")
    return bytes(buf)


def _write(path, data: bytes) -> None:
    path.write_bytes(data)


def _parse_recipe(path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# TestCookVolatileSuccess — schema 4.5 + volatile section
# ---------------------------------------------------------------------------


class TestCookVolatileSuccess:
    def test_schema_45_and_volatile_section(self, tmp_path):
        stock, so, calib = _build_stock()
        tuned = _build_tuned(stock)

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "portable.remap"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)

        assert recipe["schema_version"] == "4.5"
        assert recipe["type"] == "recipe"
        assert recipe["metadata"]["source"] == "cook_volatile"
        assert recipe["metadata"]["excluded_volatile"] is True
        assert recipe["metadata"]["instruction_count"] == len(recipe["instructions"])

        volatile = recipe["volatile"]
        assert set(volatile) == {"excluded", "flagged", "summary"}
        assert volatile["summary"]["excluded_count"] == 1
        assert volatile["summary"]["bytes_excluded"] == 6
        excluded = volatile["excluded"][0]
        assert excluded["kind"] == "VIN"
        assert excluded["action"] == "excluded"
        assert excluded["offset"] == so
        assert excluded["confidence"] >= 0.9
        assert any("VIN-structured" in e for e in excluded["evidence"])
        assert volatile["flagged"] == []

        # The VIN instruction is gone from the patch list; the calibration
        # instruction is kept.
        offsets = [inst["offset"] for inst in recipe["instructions"]]
        assert so not in offsets
        assert calib in offsets

        # Statistics recomputed over the KEPT set.
        assert recipe["statistics"]["total_changes"] == len(recipe["instructions"])
        assert recipe["statistics"]["total_bytes_changed"] == sum(
            inst["size"] for inst in recipe["instructions"]
        )

    def test_fingerprint_recomputed_over_kept_set(self, tmp_path):
        """The fingerprint must reflect the KEPT instructions — not the
        pre-exclusion set (audit Phase 3 relies on this)."""
        from openremap.core.services.recipes.recipe_builder import compute_fingerprint

        stock, so, _ = _build_stock()
        tuned = _build_tuned(stock)

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "portable.remap"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)
        assert recipe["fingerprint"] == compute_fingerprint(recipe["instructions"])
        # ... and it differs from the full (unfiltered) set's fingerprint.
        full = compute_fingerprint([i for i in recipe["instructions"]] + [{
            "offset": so, "ob": "123456", "mb": "654321",
        }])
        assert recipe["fingerprint"] != full

    def test_no_volatile_findings_is_clean(self, tmp_path):
        """A tune with no volatile changes → empty volatile section,
        schema still 4.5, nothing excluded."""
        rng = random.Random(11)
        stock = bytes(rng.randbytes(_SIZE))
        tuned = bytearray(stock)
        tuned[_CALIB_OFF] ^= 0xFF

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        output = tmp_path / "recipe.remap"
        _write(original, stock)
        _write(modified, bytes(tuned))

        result = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)
        assert recipe["schema_version"] == "4.5"
        assert recipe["volatile"]["summary"] == {
            "excluded_count": 0,
            "flagged_count": 0,
            "bytes_excluded": 0,
        }
        assert len(recipe["instructions"]) == 1


# ---------------------------------------------------------------------------
# Flag matrix
# ---------------------------------------------------------------------------


class TestFlagMatrix:
    def test_no_exclude_keeps_everything(self, tmp_path):
        stock, so, calib = _build_stock()
        tuned = _build_tuned(stock)

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "recipe.remap"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", "--no-exclude",
             str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)

        # VIN instruction stays in the patch list.
        offsets = [inst["offset"] for inst in recipe["instructions"]]
        assert so in offsets
        assert calib in offsets

        # Everything recorded as flagged; nothing excluded.
        volatile = recipe["volatile"]
        assert volatile["summary"] == {
            "excluded_count": 0,
            "flagged_count": 1,
            "bytes_excluded": 0,
        }
        assert volatile["excluded"] == []
        assert volatile["flagged"][0]["kind"] == "VIN"
        assert volatile["flagged"][0]["action"] == "flagged"
        assert recipe["metadata"]["excluded_volatile"] is False

        combined = result.stdout + result.stderr
        assert "--no-exclude" in combined

    def test_exclude_uncertain_promotes_flags(self, tmp_path):
        """An ident-block ASCII change is SERIAL_OR_IDENT (flagged) by
        default; --exclude-uncertain excludes it too."""
        stock, so, _ = _build_stock()
        tuned = bytearray(_build_tuned(stock))
        # ASCII-shaped change inside the ident block padding (not the VIN).
        pad_change = _VIN_OFF + 8
        tuned[pad_change : pad_change + 4] = b"TEST"

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        _write(original, stock)
        _write(modified, bytes(tuned))

        # Default: VIN excluded, serial/ident flagged.
        out_default = tmp_path / "default.remap"
        r1 = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(out_default)],
        )
        assert r1.exit_code == 0, r1.output
        d = _parse_recipe(out_default)
        kinds = {f["kind"] for f in d["volatile"]["excluded"]}
        flagged = {f["kind"] for f in d["volatile"]["flagged"]}
        assert kinds == {"VIN"}
        assert flagged == {"SERIAL_OR_IDENT"}
        offsets = [inst["offset"] for inst in d["instructions"]]
        assert pad_change in offsets  # flagged, still in patch list

        # --exclude-uncertain: both gone.
        out_unc = tmp_path / "uncertain.remap"
        r2 = runner.invoke(
            app,
            ["cook-volatile", "--exclude-uncertain",
             str(original), str(modified), "--output", str(out_unc)],
        )
        assert r2.exit_code == 0, r2.output
        u = _parse_recipe(out_unc)
        kinds2 = {f["kind"] for f in u["volatile"]["excluded"]}
        assert kinds2 == {"VIN", "SERIAL_OR_IDENT"}
        assert u["volatile"]["flagged"] == []
        offsets2 = [inst["offset"] for inst in u["instructions"]]
        assert pad_change not in offsets2
        assert so not in offsets2

    def test_accept_volatile_suppresses_review_list(self, tmp_path):
        stock, _, _ = _build_stock()
        tuned = _build_tuned(stock)

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", "--accept-volatile",
             str(original), str(modified)],
        )
        assert result.exit_code == 0, result.output
        combined = result.stdout + result.stderr
        # Summary always shown; per-instruction review list suppressed.
        assert "Volatile summary" in combined
        assert "excluded as volatile" not in combined
        assert "flagged for review" not in combined

    def test_help_exits_zero_and_shows_flags(self, monkeypatch):
        # Typer renders --help through a Rich console whose width comes from
        # typer.rich_utils.MAX_WIDTH (read from the TERMINAL_WIDTH env var
        # at import time — CI runners set it).  Pin a wide console so the
        # truncation point is stable; --exclude-uncertain still renders as
        # --exclude-uncert… (fixed-width option cell), so match that fragment.
        import typer.rich_utils as rich_utils

        monkeypatch.setattr(rich_utils, "MAX_WIDTH", 200)
        result = runner.invoke(app, ["cook-volatile", "--help"])
        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        for flag in ("--no-exclude", "--exclude-uncert", "--accept-volatile"):
            assert flag in combined


# ---------------------------------------------------------------------------
# Schema & consumer compat
# ---------------------------------------------------------------------------


class TestSchemaCompat:
    def test_check_schema_version_accepts_45(self, tmp_path):
        stock, _, _ = _build_stock()
        tuned = _build_tuned(stock)
        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "recipe.remap"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)
        check_schema_version(recipe)  # must not raise

    def test_plain_cook_recipe_still_tunes(self, tmp_path):
        """A 4.4 (or 4.3) recipe from plain cook still applies — the 4.5
        bump must not regress existing consumers."""
        stock, _, calib = _build_stock()
        tuned = _build_tuned(stock)
        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        recipe_path = tmp_path / "full.remap"
        _write(original, stock)
        _write(modified, tuned)

        r = runner.invoke(
            app,
            ["cook", str(original), str(modified), "--output", str(recipe_path)],
        )
        assert r.exit_code == 0, r.output
        recipe = _parse_recipe(recipe_path)
        assert recipe["schema_version"] in ("4.3", "4.4")
        check_schema_version(recipe)

        out = tmp_path / "tunedB.bin"
        t = runner.invoke(
            app, ["tune", str(original), str(recipe_path), "--output", str(out)]
        )
        assert t.exit_code == 0, t.output
        assert out.exists()


# ---------------------------------------------------------------------------
# Maps annotation runs AFTER filtering — refs index the KEPT set
# ---------------------------------------------------------------------------


class TestMapsConsistency:
    def _map_bin(self, seed: int = 21) -> tuple[bytes, int]:
        """Stock with an embedded 4x3 u16 map (like test_cli_cook) plus
        the VIN block/mirror.  Returns (data, data_off)."""
        stock, so, calib = _build_stock(seed=seed)
        buf = bytearray(stock)
        off = 0x200
        x = list(range(0, 4 * 100, 100))
        y = [0, 50, 100]
        cells = [100 + r * 10 + c for r in range(3) for c in range(4)]
        buf[off : off + 8] = __import__("struct").pack("<" + "H" * 4, *x)
        buf[off + 8 : off + 14] = __import__("struct").pack("<" + "H" * 3, *y)
        data_off = off + 14
        buf[data_off : data_off + 24] = __import__("struct").pack(
            "<" + "H" * 12, *cells
        )
        return bytes(buf), data_off

    def test_maps_refs_index_kept_set(self, tmp_path):
        stock, data_off = self._map_bin()
        tuned = bytearray(_build_tuned(stock))
        tuned[data_off] ^= 0xFF  # tune a map cell too

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "portable.remap"
        _write(original, stock)
        _write(modified, bytes(tuned))

        result = runner.invoke(
            app,
            ["cook-volatile", str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)

        assert recipe["schema_version"] == "4.5"
        assert "maps" in recipe
        n_kept = len(recipe["instructions"])
        for m in recipe["maps"]:
            assert all(1 <= r <= n_kept for r in m["instruction_refs"]), (
                "maps[].instruction_refs must index the KEPT (post-exclusion) "
                "instruction set — attach_maps must run AFTER filtering"
            )

        # The tuned map cell instruction is kept and referenced.
        cell_refs = {
            r for m in recipe["maps"] for r in m["instruction_refs"]
        }
        kept_offsets = [inst["offset"] for inst in recipe["instructions"]]
        assert data_off in kept_offsets
        assert cell_refs  # at least one map references a kept instruction

    def test_no_annotate_maps_omits_maps_section(self, tmp_path):
        stock, _ = self._map_bin()
        tuned = _build_tuned(stock)

        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        output = tmp_path / "recipe.remap"
        _write(original, stock)
        _write(modified, tuned)

        result = runner.invoke(
            app,
            ["cook-volatile", "--no-annotate-maps",
             str(original), str(modified), "--output", str(output)],
        )
        assert result.exit_code == 0, result.output
        recipe = _parse_recipe(output)
        assert "maps" not in recipe
        assert recipe["schema_version"] == "4.5"
        assert recipe["volatile"]["summary"]["excluded_count"] == 1


# ---------------------------------------------------------------------------
# Determinism — the volatile section is a pure function of the inputs
# ---------------------------------------------------------------------------


class TestVolatileDeterminism:
    def test_two_cooks_identical_volatile_section(self, tmp_path):
        stock, _, _ = _build_stock()
        tuned = _build_tuned(stock)
        original = tmp_path / "stockA.bin"
        modified = tmp_path / "tunedA.bin"
        _write(original, stock)
        _write(modified, tuned)

        recipes = []
        for i in range(2):
            out = tmp_path / f"recipe{i}.remap"
            r = runner.invoke(
                app,
                ["cook-volatile", "--compact",
                 str(original), str(modified), "--output", str(out)],
            )
            assert r.exit_code == 0, r.output
            recipes.append(_parse_recipe(out))

        assert recipes[0]["volatile"] == recipes[1]["volatile"]
        assert recipes[0]["statistics"] == recipes[1]["statistics"]


# ---------------------------------------------------------------------------
# THE Phase-2 product test — e2e portability proof
# ---------------------------------------------------------------------------


class TestPortabilityProof:
    def test_volatile_recipe_applies_to_other_car_plain_cook_fails(self, tmp_path):
        stock_a, so, calib = _build_stock()
        tuned_a = _build_tuned(stock_a)
        stock_b = _build_stock_b(stock_a)

        stockA = tmp_path / "stockA.bin"
        tunedA = tmp_path / "tunedA.bin"
        stockB = tmp_path / "stockB.bin"
        _write(stockA, stock_a)
        _write(tunedA, tuned_a)
        _write(stockB, stock_b)

        # ── 1. cook-volatile(stockA, tunedA) ──────────────────────────────
        portable = tmp_path / "portable.remap"
        r1 = runner.invoke(
            app,
            ["cook-volatile", str(stockA), str(tunedA), "--output", str(portable)],
        )
        assert r1.exit_code == 0, r1.output
        p_recipe = _parse_recipe(portable)
        assert p_recipe["schema_version"] == "4.5"
        assert p_recipe["volatile"]["summary"]["excluded_count"] == 1
        assert p_recipe["volatile"]["excluded"][0]["kind"] == "VIN"
        assert so not in [i["offset"] for i in p_recipe["instructions"]]

        # ── 2. plain cook(stockA, tunedA) — includes the VIN instruction ──
        full = tmp_path / "full.remap"
        r2 = runner.invoke(
            app,
            ["cook", str(stockA), str(tunedA), "--output", str(full)],
        )
        assert r2.exit_code == 0, r2.output
        f_recipe = _parse_recipe(full)
        assert so in [i["offset"] for i in f_recipe["instructions"]]

        # ── 3. Apply the PORTABLE recipe to stockB → clean success ────────
        out_p = tmp_path / "stockB_tuned_portable.bin"
        r3 = runner.invoke(
            app,
            ["tune", str(stockB), str(portable), "--output", str(out_p)],
        )
        assert r3.exit_code == 0, r3.output
        assert out_p.exists()
        tuned_b = out_p.read_bytes()
        # calibration byte was patched to tunedA's value
        assert tuned_b[calib] == tuned_a[calib]
        assert tuned_b[calib] != stock_b[calib]
        # VIN region untouched by the portable recipe (it wasn't patched)
        assert tuned_b[so : so + 6] == stock_b[so : so + 6]

        # ── 4. Apply the PLAIN cook recipe to stockB → hard-fails ─────────
        out_f = tmp_path / "stockB_tuned_full.bin"
        r4 = runner.invoke(
            app,
            ["tune", str(stockB), str(full), "--output", str(out_f)],
        )
        assert r4.exit_code == 1, (
            "plain cook recipe must HARD-FAIL on stockB (VIN bytes differ) — "
            f"got exit {r4.exit_code}\n{r4.output}"
        )
        assert not out_f.exists(), "failed tune must not write an output"
        combined = r4.stdout + r4.stderr
        assert "NOT safe to apply" in combined


# ---------------------------------------------------------------------------
# Phase-5 real-pair e2e — REAL tune binaries + synthetic VIN injection
# ---------------------------------------------------------------------------


class TestRealPairPortability:
    """The Phase-5 product test on real bytes: inject a synthetic VIN into
    the real tests/data/tune/ EDC17 stock, cook-volatile against the real
    tune, then prove portability across a different-VIN stockB and that the
    volatile-aware audit comes back clean."""

    _VIN_OFF = 0x800
    _MIRROR_OFF = 0x1000

    def _fixture(self, tmp_path):
        import pytest as _pytest

        base = Path(__file__).parent.parent / "data" / "tune"
        stock = base / "original.bin"
        tuned = base / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"
        if not stock.exists() or not tuned.exists():
            _pytest.skip("corpus binaries not present")
        return stock.read_bytes(), tuned.read_bytes()

    def _inject_vin(self, stock: bytes, vin: str) -> bytes:
        """Splice the VIN ident-block + mirror into the untouched head
        region of the real stock (real tune edits start at 0x225A13)."""
        buf = bytearray(stock)
        block = _PAD_A1 + vin.encode("ascii") + _PAD_A2
        buf[self._VIN_OFF : self._VIN_OFF + len(block)] = block
        mirror = _PAD_B1 + vin.encode("ascii") + _PAD_B2
        buf[self._MIRROR_OFF : self._MIRROR_OFF + len(mirror)] = mirror
        return bytes(buf)

    def _apply_real_tune(self, base: bytes, original: bytes, tuned: bytes) -> bytes:
        """Overlay the real tune's changed blocks (diffed between the
        ORIGINAL stock and the real tuned file) onto *base* — the VIN
        regions spliced into *base* survive untouched."""
        from openremap._rust import find_changed_blocks

        buf = bytearray(base)
        for off, size, _ob, _mb in find_changed_blocks(original, tuned, 16):
            buf[off : off + size] = tuned[off : off + size]
        return bytes(buf)

    def test_real_pair_portable_recipe_applies_audit_clean(self, tmp_path):
        stock, tuned = self._fixture(tmp_path)
        serial_off = self._VIN_OFF + len(_PAD_A1) + 11

        stock_a = self._inject_vin(stock, VIN_STOCK)
        # tunedA = same VIN ident block/mirror as stockA + real tune + a
        # rewritten serial (single volatile change, mirror left intact).
        tuned_a = self._apply_real_tune(stock_a, stock, tuned)
        tuned_a = bytearray(tuned_a)
        tuned_a[serial_off : serial_off + 6] = VIN_TUNED[11:17].encode("ascii")
        tuned_a = bytes(tuned_a)
        stock_b = self._inject_vin(stock, VIN_OTHER)
        stock_b = bytearray(stock_b)
        stock_b[serial_off : serial_off + 6] = VIN_OTHER[11:17].encode("ascii")
        stock_b = bytes(stock_b)

        stockA = tmp_path / "stockA.bin"
        tunedA = tmp_path / "tunedA.bin"
        stockB = tmp_path / "stockB.bin"
        stockA.write_bytes(stock_a)
        tunedA.write_bytes(tuned_a)
        stockB.write_bytes(stock_b)

        # ── cook-volatile(stockA, tunedA) → excludes the VIN instruction ──
        portable = tmp_path / "portable.remap"
        r1 = runner.invoke(
            app,
            ["cook-volatile", str(stockA), str(tunedA), "--output", str(portable)],
        )
        assert r1.exit_code == 0, r1.output
        p_recipe = _parse_recipe(portable)
        assert p_recipe["schema_version"] == "4.5"
        assert p_recipe["volatile"]["summary"]["excluded_count"] == 1
        assert serial_off not in [i["offset"] for i in p_recipe["instructions"]]

        # ── plain cook(stockA, tunedA) keeps the VIN instruction ─────────
        full = tmp_path / "full.remap"
        r2 = runner.invoke(
            app,
            ["cook", str(stockA), str(tunedA), "--output", str(full)],
        )
        assert r2.exit_code == 0, r2.output
        f_recipe = _parse_recipe(full)
        assert serial_off in [i["offset"] for i in f_recipe["instructions"]]

        # ── tune stockB with the PORTABLE recipe → clean success ─────────
        out_p = tmp_path / "stockB_portable.bin"
        r3 = runner.invoke(
            app,
            ["tune", str(stockB), str(portable), "--output", str(out_p)],
        )
        assert r3.exit_code == 0, r3.output
        assert out_p.exists()
        assert out_p.read_bytes()[serial_off : serial_off + 6] == stock_b[
            serial_off : serial_off + 6
        ]  # VIN region untouched by portable recipe

        # ── tune stockB with the PLAIN recipe → hard-fails ───────────────
        out_f = tmp_path / "stockB_full.bin"
        r4 = runner.invoke(
            app,
            ["tune", str(stockB), str(full), "--output", str(out_f)],
        )
        assert r4.exit_code == 1, (
            "plain cook recipe must hard-fail on different-VIN stockB — "
            f"got exit {r4.exit_code}\n{r4.output}"
        )
        assert not out_f.exists()

        # ── volatile-aware audit of the portable recipe → clean ──────────
        r5 = runner.invoke(app, ["audit", str(stockA), str(tunedA), str(portable)])
        assert r5.exit_code == 0, r5.output
        assert "consistent" in r5.stdout
