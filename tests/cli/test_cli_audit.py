"""Tests for the ``audit`` sub-command — end-to-end via the real CLI."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()


def _pair(patches: dict[int, int]):
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


def _write(tmp_path, stock, tuned, name_stock="stock.bin", name_tuned="tuned.bin"):
    sp = tmp_path / name_stock
    tp = tmp_path / name_tuned
    sp.write_bytes(stock)
    tp.write_bytes(tuned)
    return sp, tp


def _cook(tmp_path, sp, tp, name="t.remap"):
    result = runner.invoke(
        app,
        ["cook", "--no-annotate-maps", str(sp), str(tp), "--output", str(tmp_path / name)],
    )
    assert result.exit_code == 0
    return tmp_path / name


class TestAuditCLI:
    def test_honest_pair_reports_consistent(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)

        result = runner.invoke(app, ["audit", str(sp), str(tp), str(rp)])
        assert result.exit_code == 0
        assert "Provenance" in result.stdout
        assert "Fingerprint" in result.stdout
        assert "consistent" in result.stdout

    def test_tampered_tuned_reports_unaccounted(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)
        tampered = bytearray(tuned)
        tampered[700] ^= 0xFF
        tp.write_bytes(bytes(tampered))

        result = runner.invoke(app, ["audit", str(sp), str(tp), str(rp)])
        assert result.exit_code == 0
        assert "Unaccounted" in result.stdout
        assert "inconsistencies" in result.stdout.lower()

    def test_wrong_stock_reports_provenance_fail(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)
        other = tmp_path / "other.bin"
        other.write_bytes(os.urandom(8192))

        result = runner.invoke(app, ["audit", str(other), str(tp), str(rp)])
        assert result.exit_code == 0
        assert "Provenance" in result.stdout
        assert "FAIL" in result.stdout

    def test_json_output_shape(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)

        result = runner.invoke(
            app, ["audit", str(sp), str(tp), str(rp), "--json"]
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["provenance"]["ok"] is True
        assert out["fingerprint"]["ok"] is True
        assert out["clean"] is True
        assert out["unaccounted"]["bytes"] == 0

    def test_malformed_recipe_exits_one(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        bad = tmp_path / "bad.remap"
        bad.write_text('{"nope": 1}')

        result = runner.invoke(app, ["audit", str(sp), str(tp), str(bad)])
        assert result.exit_code == 1

    def test_size_mismatch_exits_one(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)
        tp.write_bytes(tuned + b"\x00\x00")

        result = runner.invoke(app, ["audit", str(sp), str(tp), str(rp)])
        assert result.exit_code == 1

    def test_missing_file_exits_two(self, tmp_path):
        stock, tuned = _pair({200: 0x11})
        sp, tp = _write(tmp_path, stock, tuned)
        rp = _cook(tmp_path, sp, tp)

        result = runner.invoke(
            app, ["audit", str(sp), str(tp), str(tmp_path / "nope.remap")]
        )
        assert result.exit_code == 2

    def test_help_lists_audit(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "audit" in result.stdout

    def test_real_pair_audit_is_clean(self):
        base = Path(__file__).parent.parent / "data" / "tune"
        stock = base / "original.bin"
        tuned = base / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"
        if not stock.exists() or not tuned.exists():
            pytest.skip("corpus binaries not present")
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "t.remap"
            res = runner.invoke(
                app,
                [
                    "cook",
                    "--no-annotate-maps",
                    str(stock),
                    str(tuned),
                    "--output",
                    str(rp),
                ],
            )
            assert res.exit_code == 0
            result = runner.invoke(
                app, ["audit", str(stock), str(tuned), str(rp)]
            )
            assert result.exit_code == 0
            assert "consistent" in result.stdout
