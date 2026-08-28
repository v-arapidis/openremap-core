"""
Tests for ``openremap convert <INPUT> [-o OUTPUT] [--format ...] [--json]``.

Runs through the real CLI via typer.testing.CliRunner.  Synthetic fixtures
are generated with the service encoders; the corpus-gated tests at the end
prove the raw-``.hex`` fallback and the end-to-end HEX→identify/cook path on
real binaries (skipped when ``tests/data/`` is absent, per house rules).
"""

import json

import pytest
from typer.testing import CliRunner

from openremap.core.cli.main import app
from openremap.core.services.convert import encode_ihex, encode_srec

runner = CliRunner()

DATA = "tests/data/tune/original.bin"


# ---------------------------------------------------------------------------
# Basic conversion
# ---------------------------------------------------------------------------


def test_convert_hex_to_bin(tmp_path):
    src = tmp_path / "boot.hex"
    src.write_text(encode_ihex(b"\x11\x22\x33\x44"))
    out = tmp_path / "boot.bin"
    result = runner.invoke(app, ["convert", str(src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"\x11\x22\x33\x44"
    assert "Intel HEX" in result.output


def test_convert_srec_to_bin(tmp_path):
    src = tmp_path / "flash.s19"
    src.write_text(encode_srec(b"\xde\xad\xbe\xef" * 4))
    out = tmp_path / "flash.bin"
    result = runner.invoke(app, ["convert", str(src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"\xde\xad\xbe\xef" * 4


def test_convert_bin_passthrough(tmp_path):
    src = tmp_path / "dump.bin"
    src.write_bytes(b"\x00\x01\x02\xff")
    out = tmp_path / "dump_out.bin"
    result = runner.invoke(app, ["convert", str(src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"\x00\x01\x02\xff"
    assert "raw binary" in result.output


def test_convert_default_output_name(tmp_path):
    src = tmp_path / "boot.hex"
    src.write_text(encode_ihex(b"\xaa\xbb"))
    result = runner.invoke(app, ["convert", str(src)])
    assert result.exit_code == 0, result.output
    default_out = tmp_path / "boot.bin"
    assert default_out.exists()
    assert default_out.read_bytes() == b"\xaa\xbb"


def test_convert_high_base_normalised(tmp_path):
    src = tmp_path / "flash.hex"
    src.write_text(encode_ihex(b"\x11\x22", base=0x80000000))
    out = tmp_path / "flash.bin"
    result = runner.invoke(app, ["convert", str(src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"\x11\x22"


def test_convert_json_summary(tmp_path):
    src = tmp_path / "boot.hex"
    src.write_text(encode_ihex(b"\x11\x22\x33"))
    out = tmp_path / "boot.bin"
    result = runner.invoke(app, ["convert", str(src), "-o", str(out), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["format"] == "ihex"
    assert payload["size"] == 3
    assert payload["output"].endswith("boot.bin")
    assert payload["warnings"] == []


# ---------------------------------------------------------------------------
# --format override
# ---------------------------------------------------------------------------


def test_convert_format_bin_forces_raw(tmp_path):
    """A valid HEX file forced to bin must come out as raw text bytes."""
    src = tmp_path / "tricky.hex"
    src.write_text(encode_ihex(b"\x11"))
    out = tmp_path / "tricky.bin"
    result = runner.invoke(
        app, ["convert", str(src), "-o", str(out), "--format", "bin"]
    )
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == src.read_text().encode()


def test_convert_format_ihex_strict_on_garbage(tmp_path):
    src = tmp_path / "garbage.hex"
    src.write_bytes(b":" + b"\x00" * 32)
    out = tmp_path / "garbage.bin"
    result = runner.invoke(
        app, ["convert", str(src), "-o", str(out), "--format", "ihex"]
    )
    assert result.exit_code == 1
    assert not out.exists()
    assert "invalid ihex data" in result.stderr or "invalid ihex data" in result.output


def test_convert_invalid_format_choice(tmp_path):
    src = tmp_path / "x.hex"
    src.write_text(encode_ihex(b"\x11"))
    result = runner.invoke(app, ["convert", str(src), "--format", "elf"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_convert_bad_checksum_exits_one(tmp_path):
    lines = encode_ihex(b"\x11\x22\x33\x44").strip().splitlines()
    first = lines[0]
    flipped = "00" if first[-2:] != "00" else "01"
    src = tmp_path / "bad.hex"
    src.write_text("\n".join([first[:-2] + flipped] + lines[1:]) + "\n")
    result = runner.invoke(app, ["convert", str(src), "-o", str(tmp_path / "o.bin")])
    assert result.exit_code == 1
    assert "invalid" in (result.stderr + result.output).lower()


def test_convert_missing_file_exits_two(tmp_path):
    result = runner.invoke(app, ["convert", str(tmp_path / "nope.hex")])
    assert result.exit_code == 2  # Click: exists=True


def test_convert_empty_file_exits_one(tmp_path):
    src = tmp_path / "empty.hex"
    src.write_bytes(b"")
    result = runner.invoke(app, ["convert", str(src), "-o", str(tmp_path / "o.bin")])
    assert result.exit_code == 1
    assert "empty" in (result.stderr + result.output).lower()


# ---------------------------------------------------------------------------
# Corpus-gated: the raw-.hex fallback and the end-to-end trigger proof
# ---------------------------------------------------------------------------


def _corpus_pair():
    """Return (stock, tuned) paths from the real corpus or skip."""
    stock = "tests/data/tune/original.bin"
    if not __import__("os").path.exists(stock):
        pytest.skip("tests/data/tune corpus pair missing")
    return stock, "tests/data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin"


def test_identify_raw_dot_hex_dump_unchanged(tmp_path):
    """A raw dump named .hex (Subaru convention) must identify as before.

    The file contains raw bytes, not Intel HEX text — the content sniffer
    must not mangle it (it either never sniffs as HEX, or falls back to raw
    with a warning if the first byte happens to be ':'/'S').
    """
    stock, _tuned = _corpus_pair()
    hex_named = tmp_path / "dump.hex"
    hex_named.write_bytes(open(stock, "rb").read())

    raw_result = runner.invoke(app, ["identify", stock, "--json"])
    hex_result = runner.invoke(app, ["identify", str(hex_named), "--json"])
    assert raw_result.exit_code == 0
    assert hex_result.exit_code == 0, hex_result.output

    from_raw = json.loads(raw_result.output)
    from_hex = json.loads(hex_result.output)
    assert from_hex["match_key"] == from_raw["match_key"]
    assert from_hex["manufacturer"] == from_raw["manufacturer"]


def test_convert_real_binary_to_hex_and_back(tmp_path):
    """The 4 MB EDC17 stock converts to Intel HEX and back byte-identically."""
    stock, _tuned = _corpus_pair()
    raw = open(stock, "rb").read()

    hex_src = tmp_path / "orig.hex"
    hex_src.write_text(encode_ihex(raw))
    out = tmp_path / "orig_back.bin"
    result = runner.invoke(app, ["convert", str(hex_src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == raw


def test_identify_hex_matches_raw_identify(tmp_path):
    """identify on a real Intel HEX file must match the raw-binary run.

    Goes through the CLI so the HEX file is decoded by the input boundary
    (content sniffing), exactly as a user would hit it.
    """
    stock, _tuned = _corpus_pair()
    hex_src = tmp_path / "orig.hex"
    hex_src.write_text(encode_ihex(open(stock, "rb").read()))

    raw_result = runner.invoke(app, ["identify", stock, "--json"])
    hex_result = runner.invoke(app, ["identify", str(hex_src), "--json"])
    assert raw_result.exit_code == 0
    assert hex_result.exit_code == 0, hex_result.output

    from_raw = json.loads(raw_result.output)
    from_hex = json.loads(hex_result.output)
    assert from_hex["match_key"] == from_raw["match_key"]
    assert from_hex["ecu_family"] == from_raw["ecu_family"]
