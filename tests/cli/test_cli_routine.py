"""
Tests for the ``routine`` sub-command (the pseudo-decompiler).

Corpus-gated tests skip cleanly when ``tests/data/`` is absent; the
synthetic tests always run (the renderer is not corpus-dependent).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()


def test_routine_help_exits_zero():
    result = runner.invoke(app, ["routine", "--help"])
    assert result.exit_code == 0
    assert "routine" in result.stdout


def test_routine_unknown_family_hints_arch(tmp_path: Path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"\x00" * 1024)
    result = runner.invoke(app, ["routine", str(f), "0x10"])
    assert result.exit_code == 1
    assert "--arch" in result.stderr


def test_routine_arch_override_renders(tmp_path: Path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"\x90" * 64)  # x86 NOP sled
    result = runner.invoke(app, ["routine", str(f), "0x10", "--arch", "x86"])
    assert result.exit_code == 0
    assert "nop" in result.stdout.lower()


def test_routine_invalid_offset_fails(tmp_path: Path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"\x00" * 64)
    result = runner.invoke(app, ["routine", str(f), "notanoffset"])
    assert result.exit_code != 0


def test_routine_me7_corpus():
    d = Path("tests/data/ECUs/Bosch/ME7.5")
    if not d.is_dir():
        pytest.skip("ME7.5 corpus absent")
    bins = sorted(d.glob("*.ori")) + sorted(d.glob("*.bin"))
    if not bins:
        pytest.skip("no ME7.5 files")
    result = runner.invoke(app, ["routine", str(bins[0]), "0x50000", "--after", "8"])
    assert result.exit_code == 0
    assert "(c166)" in result.stdout
    assert ">>" in result.stdout
