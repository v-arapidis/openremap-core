"""Tests for the ``layout`` sub-command."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()

_DATA = Path(__file__).parent.parent / "data" / "ECUs"


class TestLayoutCLI:
    def test_layout_synthetic_bin(self, tmp_path):
        # 3 sectors: code | erased FF | C3-erased
        data = os.urandom(0x10000) + b"\xFF" * 0x10000 + b"\xC3" * 0x10000
        target = tmp_path / "bin.bin"
        target.write_bytes(data)

        result = runner.invoke(app, ["layout", str(target)])
        assert result.exit_code == 0
        assert "Flash-Layout Segmentation" in result.stdout
        assert "code" in result.stdout
        assert "erased" in result.stdout

    def test_layout_json(self, tmp_path):
        data = b"\xFF" * 0x10000 + os.urandom(0x10000)
        target = tmp_path / "bin.bin"
        target.write_bytes(data)

        result = runner.invoke(app, ["layout", str(target), "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["file_size"] == len(data)
        kinds = [r["kind"] for r in out["regions"]]
        assert kinds == ["erased", "code"]

    def test_layout_real_edc15(self):
        rel = _DATA / "Bosch/EDC15/Audi A4 2.5TDI 163HP 8E0907401AF 0281012142 375555__1__1.ori"
        if not rel.exists():
            pytest.skip("corpus binary not present")
        result = runner.invoke(app, ["layout", str(rel), "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        kinds = [r["kind"] for r in out["regions"]]
        assert "erased" in kinds and "code" in kinds and "calibration" in kinds
        assert out["ident_blocks"], "ident blocks expected on a real ECU"

    def test_missing_file_exits_two(self, tmp_path):
        result = runner.invoke(app, ["layout", str(tmp_path / "nope.bin")])
        assert result.exit_code == 2

    def test_help_lists_layout(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "layout" in result.stdout
