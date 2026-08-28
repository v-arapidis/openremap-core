"""Tests for the ``scan-vins`` sub-command."""

from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()


def _bin_with_vin(vin: str) -> bytes:
    blob = bytearray(os.urandom(0x400))
    ident = b"IDENT-METADATA-BLOCK  " + vin.encode() + b"  " * 40
    blob[0x100 : 0x100 + len(ident)] = ident
    return bytes(blob)


class TestScanVinsCLI:
    def test_finds_valid_vin(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_bin_with_vin("WAUZZZ8LXX1234567"))

        result = runner.invoke(app, ["scan-vins", str(target)])
        assert result.exit_code == 0
        assert "WAUZZZ8LXX1234567" in result.stdout
        assert "wmi" in result.stdout

    def test_json_output(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_bin_with_vin("WAUZZZ8LXX1234567"))

        result = runner.invoke(app, ["scan-vins", str(target), "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["candidates"][0]["vin"] == "WAUZZZ8LXX1234567"
        assert out["candidates"][0]["confidence"] >= 0.8

    def test_json_includes_decode(self, tmp_path):
        """Candidates are vininfo-decoded: manufacturer + checksum in JSON."""
        target = tmp_path / "ecu.bin"
        target.write_bytes(_bin_with_vin("WAUZZZ8LXX1234567"))

        result = runner.invoke(app, ["scan-vins", str(target), "--json"])
        assert result.exit_code == 0
        cand = json.loads(result.stdout)["candidates"][0]
        assert cand["manufacturer"] == "Audi"  # WAU → Audi (vininfo table)
        assert isinstance(cand["checksum_valid"], bool)
        assert cand["decoded"] is True

    def test_human_output_shows_decoded_make(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_bin_with_vin("WAUZZZ8LXX1234567"))

        result = runner.invoke(app, ["scan-vins", str(target)])
        assert result.exit_code == 0
        assert "Audi" in result.stdout
        assert "decoded, unverified" in result.stdout

    def test_min_confidence_filter(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(_bin_with_vin("WAUZZZ8LXX1234567"))

        result = runner.invoke(
            app, ["scan-vins", str(target), "--min-confidence", "0.99"]
        )
        assert result.exit_code == 0
        assert "No VIN candidates" in result.stdout

    def test_no_candidates_message(self, tmp_path):
        target = tmp_path / "ecu.bin"
        target.write_bytes(os.urandom(0x400))

        result = runner.invoke(app, ["scan-vins", str(target)])
        assert result.exit_code == 0
        assert "No VIN candidates" in result.stdout

    def test_missing_file_exits_two(self, tmp_path):
        result = runner.invoke(app, ["scan-vins", str(tmp_path / "nope.bin")])
        assert result.exit_code == 2

    def test_help_lists_scan_vins(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan-vins" in result.stdout
