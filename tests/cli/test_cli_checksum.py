"""Tests for the ``checksum`` sub-command."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.cli.main import app

runner = CliRunner()

_DATA = Path(__file__).parent.parent / "data" / "ECUs"


class TestChecksumCLI:
    def test_me7_bin_reports_main_and_multipoint(self):
        target = _DATA / "Bosch" / "ME7.1" / "066906032E.bin"
        if not target.exists():
            pytest.skip("ME7Sum corpus not present")
        result = runner.invoke(app, ["checksum", str(target)])
        assert result.exit_code == 0
        assert "ME7 main checksum: OK" in result.stdout
        assert "multipoint" in result.stdout

    def test_me7_json_output(self):
        target = _DATA / "Bosch" / "ME7.1" / "066906032E.bin"
        if not target.exists():
            pytest.skip("ME7Sum corpus not present")
        result = runner.invoke(app, ["checksum", str(target), "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["me7_main"]["status"] == "ok"
        assert out["me7_multipoint"]["valid"] >= 4

    def test_me7_ironfelix_profile_in_json(self):
        target = _DATA / "Bosch" / "ME7.1" / "066906032E.bin"
        if not target.exists():
            pytest.skip("ME7Sum corpus not present")
        result = runner.invoke(app, ["checksum", str(target), "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        families = [p["family"] for p in out["ironfelix"]]
        assert "vag_me7xx" in families
        profile = next(p for p in out["ironfelix"] if p["family"] == "vag_me7xx")
        assert profile["checks_ok"] == profile["checks_total"] >= 3
        assert {"name", "status", "stored", "expected"} <= set(profile["checks"][0])

    def test_me7_ironfelix_profile_in_text(self):
        target = _DATA / "Bosch" / "ME7.1" / "066906032E.bin"
        if not target.exists():
            pytest.skip("ME7Sum corpus not present")
        result = runner.invoke(app, ["checksum", str(target)])
        assert result.exit_code == 0
        assert "IronFelix family profiles" in result.stdout
        assert "VAG Bosch ME7.XX" in result.stdout


    def test_random_file_no_me7_and_no_page_schemes(self, tmp_path):
        # Whole-file 1-byte matches are expected noise on random data
        # (p ~ 1/256 per combo) — the strong signals (ME7, per-page
        # schemes) must be absent.
        target = tmp_path / "bin.bin"
        target.write_bytes(os.urandom(0x40000))
        result = runner.invoke(app, ["checksum", str(target)])
        assert result.exit_code == 0
        assert "ME7" not in result.stdout
        assert "page" not in result.stdout

    def test_me7_rolling_in_json_and_text(self):
        target = _DATA / "Bosch" / "ME7.1" / "8D0907551M-0001.bin"
        if not target.exists():
            pytest.skip("ME7Sum corpus not present")
        result = runner.invoke(app, ["checksum", str(target)])
        assert result.exit_code == 0
        assert "ME7 rolling: 3/3" in result.stdout
        result = runner.invoke(app, ["checksum", str(target), "--json"])
        out = json.loads(result.stdout)
        assert len(out["me7_rolling"]) == 3
        assert all(e["status"] == "ok" for e in out["me7_rolling"])

    def test_ms43_in_text_and_json(self):
        target = _DATA / "Siemens" / "MS43" / "MS43_WBABW510X0PK46741_430069_512KB.bin"
        if not target.exists():
            pytest.skip("MS43 corpus not present")
        result = runner.invoke(app, ["checksum", str(target)])
        assert result.exit_code == 0
        assert "Siemens MS43 CRC16: 3/3 sections ok" in result.stdout
        assert "monitor sum" in result.stdout
        result = runner.invoke(app, ["checksum", str(target), "--json"])
        out = json.loads(result.stdout)
        assert out["ms43"]["ok"] == 3 and out["ms43"]["total"] == 3
        assert len(out["ms43"]["mons"]) == 2

    def test_missing_file_exits_two(self, tmp_path):
        result = runner.invoke(app, ["checksum", str(tmp_path / "nope.bin")])
        assert result.exit_code == 2

    def test_help_lists_checksum(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "checksum" in result.stdout
