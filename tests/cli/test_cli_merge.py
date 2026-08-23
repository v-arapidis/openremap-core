"""Tests for the ``merge`` sub-command — end-to-end via the real CLI."""

from __future__ import annotations

import json
import random

from typer.testing import CliRunner

from openremap.cli.main import app

runner = CliRunner()


def _triple(patches_a: dict[int, int], patches_b: dict[int, int]):
    """Random 8 KB stock + two tuned variants.

    Seeded (not os.urandom): os.urandom made these tests flaky — if the
    random stock already contained a patch byte at the target offset, that
    variant produced no instruction and merges expected 2 but got 1
    (~0.8% of CI runs, 2026-08-23).
    """
    stock = bytearray(random.Random(0).randbytes(8192))
    mod_a = bytearray(stock)
    mod_b = bytearray(stock)
    for off, val in patches_a.items():
        mod_a[off] = val
    for off, val in patches_b.items():
        mod_b[off] = val
    return bytes(stock), bytes(mod_a), bytes(mod_b)


def _write(tmp_path, stock, mod_a, mod_b):
    sp = tmp_path / "stock.bin"
    ap = tmp_path / "a.bin"
    bp = tmp_path / "b.bin"
    sp.write_bytes(stock)
    ap.write_bytes(mod_a)
    bp.write_bytes(mod_b)
    return sp, ap, bp


def _cook(tmp_path, stock_path, mod_path, name):
    result = runner.invoke(
        app,
        [
            "cook",
            "--no-annotate-maps",
            str(stock_path),
            str(mod_path),
            "--output",
            str(tmp_path / name),
        ],
    )
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


class TestMergeCLI:
    def test_merge_disjoint_recipes(self, tmp_path):
        stock, mod_a, mod_b = _triple({200: 0x11}, {400: 0x22})
        sp, ap, bp = _write(tmp_path, stock, mod_a, mod_b)
        ra = _cook(tmp_path, sp, ap, "a.remap")
        rb = _cook(tmp_path, sp, bp, "b.remap")

        result = runner.invoke(
            app,
            [
                "merge",
                str(ra),
                str(rb),
                "--stock",
                str(sp),
                "--output",
                str(tmp_path / "merged.remap"),
            ],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads((tmp_path / "merged.remap").read_text())
        assert data["schema_version"] == "4.4"
        assert data["metadata"]["merged_from"] == ["a.remap", "b.remap"]
        assert len(data["instructions"]) == 2
        assert "Merged a.remap" in result.stdout

    def test_conflict_exits_one(self, tmp_path):
        stock, mod_a, mod_b = _triple({200: 0x11}, {200: 0x99})
        sp, ap, bp = _write(tmp_path, stock, mod_a, mod_b)
        ra = _cook(tmp_path, sp, ap, "a.remap")
        rb = _cook(tmp_path, sp, bp, "b.remap")

        result = runner.invoke(
            app, ["merge", str(ra), str(rb), "--stock", str(sp)]
        )
        assert result.exit_code == 1
        assert "Conflict" in result.stderr or "Conflict" in result.stdout

    def test_merge_without_stock_same_sha_succeeds(self, tmp_path):
        stock, mod_a, mod_b = _triple({200: 0x11}, {400: 0x22})
        sp, ap, bp = _write(tmp_path, stock, mod_a, mod_b)
        ra = _cook(tmp_path, sp, ap, "a.remap")
        rb = _cook(tmp_path, sp, bp, "b.remap")

        result = runner.invoke(
            app, ["merge", str(ra), str(rb), "--output", str(tmp_path / "m.remap")]
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads((tmp_path / "m.remap").read_text())
        assert data["schema_version"] == "4.3"

    def test_strict_aborts_on_stock_mismatch(self, tmp_path):
        stock, mod_a, mod_b = _triple({200: 0x11}, {400: 0x22})
        sp, ap, bp = _write(tmp_path, stock, mod_a, mod_b)
        ra = _cook(tmp_path, sp, ap, "a.remap")
        rb = _cook(tmp_path, sp, bp, "b.remap")

        # Corrupt B's recipe: an instruction that no longer matches stock.
        data_b = json.loads(rb.read_text())
        data_b["instructions"][0]["ob"] = "DEAD"
        rb.write_text(json.dumps(data_b))

        result = runner.invoke(
            app, ["merge", str(ra), str(rb), "--stock", str(sp), "--strict"]
        )
        assert result.exit_code == 1
        assert "strict" in (result.stderr + result.stdout)

        # Non-strict: succeeds with a warning, instruction skipped.
        result = runner.invoke(
            app,
            [
                "merge",
                str(ra),
                str(rb),
                "--stock",
                str(sp),
                "--output",
                str(tmp_path / "m2.remap"),
            ],
        )
        assert result.exit_code == 0
        data = json.loads((tmp_path / "m2.remap").read_text())
        assert any("skipped" in w for w in data["ecu"]["cook_warnings"])

    def test_malformed_recipe_exits_one(self, tmp_path):
        stock, mod_a, mod_b = _triple({200: 0x11}, {400: 0x22})
        sp, ap, bp = _write(tmp_path, stock, mod_a, mod_b)
        ra = _cook(tmp_path, sp, ap, "a.remap")
        bad = tmp_path / "bad.remap"
        bad.write_text('{"nope": true}')

        result = runner.invoke(
            app, ["merge", str(ra), str(bad), "--stock", str(sp)]
        )
        assert result.exit_code == 1

    def test_help_shows_merge(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "merge" in result.stdout
