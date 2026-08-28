"""Tests for the ``openremap analyze`` command."""

from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()

_ZERO_BIN = b"\x00" * 1024


class TestAnalyzeCLI:
    def test_fast_mode_exits_zero_and_reports_identity(self, tmp_path):
        f = tmp_path / "zero.bin"
        f.write_bytes(_ZERO_BIN)
        result = runner.invoke(app, ["analyze", str(f), "--fast"])
        assert result.exit_code == 0, result.output
        assert "Identity" in result.stdout
        assert "fast mode" in result.stdout

    def test_json_structure(self, tmp_path):
        f = tmp_path / "zero.bin"
        f.write_bytes(_ZERO_BIN)
        result = runner.invoke(app, ["analyze", str(f), "--fast", "--json"])
        assert result.exit_code == 0, result.output
        d = json.loads(result.stdout)
        for key in (
            "container", "file_size", "sha256", "identity", "confidence",
            "vin", "hardware", "layout", "maps", "checksums", "health", "fast",
        ):
            assert key in d
        assert d["fast"] is True

    def test_missing_file_exits_two(self, tmp_path):
        result = runner.invoke(app, ["analyze", str(tmp_path / "nope.bin")])
        assert result.exit_code == 2

    def test_empty_file_exits_one(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = runner.invoke(app, ["analyze", str(f)])
        assert result.exit_code == 1

    def test_output_file_written(self, tmp_path):
        f = tmp_path / "zero.bin"
        f.write_bytes(_ZERO_BIN)
        out = tmp_path / "report.txt"
        result = runner.invoke(app, ["analyze", str(f), "--fast", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "Identity" in out.read_text()

    def test_help_lists_analyze(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "analyze" in result.stdout

    def test_corpus_edc17_full_analyze(self, tmp_path):
        """Real corpus: full analyze on the 4 MB EDC17 stock."""
        if not os.path.exists("tests/data/tune/original.bin"):
            import pytest

            pytest.skip("tests/data/tune corpus pair missing")
        result = runner.invoke(
            app, ["analyze", "tests/data/tune/original.bin", "--json"]
        )
        assert result.exit_code == 0, result.output
        d = json.loads(result.stdout)
        assert d["identity"]["ecu_family"] == "EDC17"
        assert d["layout"]["regions"]  # layout segmented
        assert d["maps"]["axis_count"] > 0
        assert d["health"] is not None

    def test_corpus_golf5_vin_decoded(self):
        """Real corpus: the MED9 Golf 5 ROM carries a decodable VIN."""
        golf = (
            "tests/data/ECUs/Bosch/MED9/"
            "VW golf5 2.0TFSI 1K0907115K 0261S02332 380991__1__1.ori"
        )
        if not os.path.exists(golf):
            import pytest

            pytest.skip("MED9 Golf 5 corpus file missing")
        result = runner.invoke(app, ["analyze", golf, "--json"])
        assert result.exit_code == 0, result.output
        vin = json.loads(result.stdout)["vin"]
        assert vin is not None
        assert vin["manufacturer"] == "Volkswagen"
