"""
Tests for the ``scan-maps`` sub-command — region parsing and end-to-end runs.

Covered:
    - ``_parse_region``: 0x-prefixed, bare hex, leading zeros, mixed
      prefixes, ``..`` separator, invalid input, inverted range
    - ``scan-maps`` end-to-end with ``--region`` (exit 0, JSON output)
"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from openremap.cli.commands.scan_maps import _parse_region
from openremap.cli.main import app

runner = CliRunner()


def _make_bin(size: int = 4096) -> bytes:
    """Return a zero-filled byte string."""
    return bytes(size)


class TestParseRegion:
    """Unit tests for ``_parse_region`` — hex-always semantics."""

    def test_none_returns_none(self):
        assert _parse_region(None) is None

    def test_documented_0x_form(self):
        assert _parse_region("0x10000-0x80000") == slice(0x10000, 0x80000)

    def test_bare_hex(self):
        assert _parse_region("10000-80000") == slice(0x10000, 0x80000)

    def test_leading_zero_hex(self):
        assert _parse_region("0x010000-0x080000") == slice(0x10000, 0x80000)

    def test_bare_hex_leading_zeros(self):
        assert _parse_region("010000-080000") == slice(0x10000, 0x80000)

    def test_mixed_prefixes(self):
        assert _parse_region("0x10000-80000") == slice(0x10000, 0x80000)
        assert _parse_region("10000-0x80000") == slice(0x10000, 0x80000)

    def test_dotdot_separator(self):
        assert _parse_region("0x1000..0x2000") == slice(0x1000, 0x2000)

    def test_hex_letters(self):
        assert _parse_region("0x1a000-0x8b000") == slice(0x1A000, 0x8B000)

    def test_uppercase_prefix(self):
        assert _parse_region("0X1000-0X2000") == slice(0x1000, 0x2000)

    def test_single_value_rejected(self):
        with pytest.raises(typer.Exit) as exc:
            _parse_region("0x10000")
        assert exc.value.exit_code == 1

    def test_invalid_hex_rejected(self):
        with pytest.raises(typer.Exit) as exc:
            _parse_region("0xzzzz-0x1000")
        assert exc.value.exit_code == 1

    def test_inverted_range_rejected(self):
        with pytest.raises(typer.Exit) as exc:
            _parse_region("0x80000-0x10000")
        assert exc.value.exit_code == 1


class TestScanMapsRegion:
    """End-to-end: --region wiring through the CLI."""

    def test_scan_maps_with_region(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_make_bin())

        result = runner.invoke(
            app,
            ["scan-maps", str(target), "--region", "0x100-0x800", "--json"],
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["file"] == str(target)

    def test_scan_maps_region_parse_error(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_make_bin())

        result = runner.invoke(
            app,
            ["scan-maps", str(target), "--region", "0x010000-0x080000", "--json"],
        )

        # Leading-zero hex must not crash the command with an unhandled
        # traceback — it parses fine now (hex-always).
        assert result.exit_code == 0
        assert "traceback" not in (result.stdout + result.stderr).lower()

    def test_scan_maps_region_out_of_range_no_panic(self, tmp_path):
        """A region beyond the file size must not panic the Rust backend."""
        target = tmp_path / "ecu.bin"
        target.write_bytes(_make_bin())

        result = runner.invoke(
            app,
            [
                "scan-maps",
                str(target),
                "--region",
                "0x1000000-0x1010000",
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert "traceback" not in (result.stdout + result.stderr).lower()
        out = json.loads(result.stdout)
        assert out["axes_count"] == 0
        assert out["tables_count"] == 0

    def test_diff_maps_with_region(self, tmp_path):
        stock = tmp_path / "stock.bin"
        tuned = tmp_path / "tuned.bin"
        stock.write_bytes(_make_bin())
        tuned.write_bytes(_make_bin())

        result = runner.invoke(
            app,
            [
                "diff-maps",
                str(stock),
                str(tuned),
                "--region",
                "0x100-0x800",
                "--json",
            ],
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["stock"] == str(stock)
        assert out["tuned"] == str(tuned)


class TestBoLabel:
    """Endianness label helper — LE/BE, not truncated words."""

    def test_little_endian_label(self):
        from openremap.cli.commands.diff_maps import _bo_label

        assert _bo_label("little") == "LE"

    def test_big_endian_label(self):
        from openremap.cli.commands.diff_maps import _bo_label

        assert _bo_label("big") == "BE"


class TestScanMapsBatchJson:
    """Batch mode --json must emit pure JSON on stdout."""

    def test_batch_json_is_pure_json(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(bytes(4096))
        (tmp_path / "b.bin").write_bytes(bytes(2048))

        result = runner.invoke(app, ["scan-maps", str(tmp_path), "--json"])

        assert result.exit_code == 0
        out = json.loads(result.stdout)  # must not raise
        assert out["files_scanned"] == 2
        assert set(out["health"]) == {"genuine", "few", "sparse"}
        assert len(out["results"]) == 2

    def test_batch_json_with_verbose_is_pure_json(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(bytes(4096))

        result = runner.invoke(
            app, ["scan-maps", str(tmp_path), "--json", "--verbose"]
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["files_scanned"] == 1

    def test_batch_json_empty_file_recorded_as_error(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(bytes(4096))
        (tmp_path / "empty.bin").write_bytes(b"")

        result = runner.invoke(app, ["scan-maps", str(tmp_path), "--json"])

        out = json.loads(result.stdout)
        assert out["files_scanned"] == 2
        assert len(out["errors"]) == 1
        assert "EMPTY" in out["errors"][0]["error"]
        assert len(out["results"]) == 1

    def test_batch_json_recursive(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.bin").write_bytes(bytes(4096))
        (tmp_path / "sub" / "b.bin").write_bytes(bytes(2048))

        result = runner.invoke(
            app, ["scan-maps", str(tmp_path), "--json", "--recursive"]
        )

        out = json.loads(result.stdout)
        assert out["files_scanned"] == 2

    def test_batch_json_with_export_is_pure_json(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(bytes(4096))

        result = runner.invoke(
            app,
            [
                "scan-maps",
                str(tmp_path),
                "--json",
                "--export",
                str(tmp_path / "csv_out"),
            ],
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["files_scanned"] == 1

    def test_batch_human_mode_unchanged(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(bytes(4096))

        result = runner.invoke(app, ["scan-maps", str(tmp_path)])

        assert result.exit_code == 0
        assert "Batch Map Scanner" in result.stdout
        assert "Summary" in result.stdout


class TestScanMapsClassify:
    """--classify annotates tables with content labels."""

    def test_classify_human_output_shows_label_column(self, tmp_path):
        import struct

        buf = bytearray(4096)
        rpm = [500, 1000, 1500, 2000, 2500, 3000]
        load = [10, 20, 30, 40]
        o = 0x100
        buf[o : o + 12] = struct.pack("<6H", *rpm)
        o += 12
        buf[o : o + 8] = struct.pack("<4H", *load)
        o += 8
        for yi in range(4):
            for xi in range(6):
                struct.pack_into("<H", buf, o, 200 + yi * 40 - xi * 3)
                o += 2
        target = tmp_path / "ecu.bin"
        target.write_bytes(bytes(buf))

        result = runner.invoke(
            app, ["scan-maps", str(target), "--classify", "--min-score", "0.4"],
        )

        assert result.exit_code == 0
        assert "Label" in result.stdout

    def test_classify_json_has_label_fields(self, tmp_path):
        import struct

        buf = bytearray(4096)
        rpm = [500, 1000, 1500, 2000, 2500, 3000]
        load = [10, 20, 30, 40]
        o = 0x100
        buf[o : o + 12] = struct.pack("<6H", *rpm)
        o += 12
        buf[o : o + 8] = struct.pack("<4H", *load)
        o += 8
        for yi in range(4):
            for xi in range(6):
                struct.pack_into("<H", buf, o, 200 + yi * 40 - xi * 3)
                o += 2
        target = tmp_path / "ecu.bin"
        target.write_bytes(bytes(buf))

        result = runner.invoke(
            app,
            ["scan-maps", str(target), "--classify", "--min-score", "0.4", "--json"],
        )

        assert result.exit_code == 0
        out = json.loads(result.stdout)
        for t in out["tables"]:
            assert "label" in t
            assert "label_confidence" in t
            assert t["label"] in ("fuel", "timing", "boost", "torque", "duration", "unknown")
