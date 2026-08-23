"""
Siemens MS43 checksum tests (cracked 2026-08-15 via boot-code disassembly).

Corpus validation (skip-guarded): the factory base verifies 3/3 CRC16
sections; the tuned files in the corpus (calibration edits without
checksum recalculation) verify boot+program OK and calibration STALE.
Synthetic: a builder that produces a fully verifying MS43-style file,
plus corruption and recognition tests.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from openremap.core.services.checksums.ms43 import (
    _BOOT_SLOT,
    _CAL_SLOT,
    _PROG_SLOT,
    crc16_arc,
    detect_ms43,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_MS43 = (DATA / "ECUs" / "Siemens" / "MS43").is_dir()

_MS43_FILES = [
    "MS43_WBABW510X0PK46741_430069_512KB.bin",
    "MS43_430069_mod_efan.bin",
    "MS43_430069_mod_cruise.bin",
    "MS43_430069_mod_combined.bin",
]


def _w16(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<H", data, off, value & 0xFFFF)


def _w16be(data: bytearray, off: int, value: int) -> None:
    struct.pack_into(">H", data, off, value & 0xFFFF)


def _w32(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", data, off, value & 0xFFFFFFFF)


def build_ms43(seed: int = 1) -> bytes:
    """A 512 KB MS43-style file whose 3 CRC16 sections all verify."""
    n = 0x80000
    data = bytearray(os.urandom(n))

    def setup(slot: int, init_off: int, init: int, blocks, mem_base: int):
        _w16(data, slot + 2, len(blocks))
        for i, (s, e) in enumerate(blocks):
            off = slot + 4 + i * 8
            _w32(data, off, s + mem_base)
            _w32(data, off + 4, e + mem_base)
        _w16be(data, init_off, init)
        stored = crc16_arc(bytes(data), list(blocks), init)
        _w16(data, slot, stored)

    setup(_BOOT_SLOT, 0x3FE6, 0x2D2D, [(0x100, 0x2FF)], 0)
    setup(_PROG_SLOT, 0x3C34, 0x3030, [(0x10000, 0x103FF)], 0x80000)
    setup(_CAL_SLOT, 0x6FFBF, 0x3936, [(0x70000, 0x702FF)], 0)
    return bytes(data)


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------


def test_synthetic_ms43_all_ok():
    profile = detect_ms43(build_ms43())
    assert profile is not None
    assert [c.status for c in profile.crcs] == ["ok", "ok", "ok"]
    assert profile.ok == 3


def test_synthetic_ms43_corruption_stale():
    data = bytearray(build_ms43())
    data[0x200] ^= 0xFF  # inside the boot block
    profile = detect_ms43(bytes(data))
    assert profile is not None
    statuses = {c.name: c.status for c in profile.crcs}
    assert statuses["boot"] == "stale"
    assert statuses["program"] == "ok"
    assert statuses["calibration"] == "ok"


def test_synthetic_ms43_program_block_memory_coords():
    """Program blocks are stored as memory addresses (file + 0x80000)."""
    profile = detect_ms43(build_ms43())
    assert profile is not None
    prog = next(c for c in profile.crcs if c.name == "program")
    assert prog.blocks == ((0x10000, 0x103FF),)


def test_ms43_wrong_size_rejected():
    data = build_ms43()
    assert detect_ms43(data[: len(data) - 1]) is None


def test_ms43_random_file_rejected():
    assert detect_ms43(os.urandom(0x80000)) is None


# ---------------------------------------------------------------------------
# Corpus (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_MS43, reason="tests/data/ECUs/Siemens/MS43 corpus missing")
def test_corpus_factory_base_all_ok():
    data = (DATA / "ECUs" / "Siemens" / "MS43" / _MS43_FILES[0]).read_bytes()
    profile = detect_ms43(data)
    assert profile is not None
    assert [c.status for c in profile.crcs] == ["ok", "ok", "ok"]
    # mon slots reported but unverified
    assert [m.status for m in profile.mons] == ["unverified", "unverified"]
    assert all(m.stored is not None for m in profile.mons)


@pytest.mark.skipif(not HAS_MS43, reason="tests/data/ECUs/Siemens/MS43 corpus missing")
@pytest.mark.parametrize("filename", _MS43_FILES[1:])
def test_corpus_tuned_files_cal_stale(filename):
    """The tuned files edit calibration bytes without recalculating the
    checksum -> boot/program OK, calibration STALE."""
    data = (DATA / "ECUs" / "Siemens" / "MS43" / filename).read_bytes()
    profile = detect_ms43(data)
    assert profile is not None
    statuses = {c.name: c.status for c in profile.crcs}
    assert statuses["boot"] == "ok"
    assert statuses["program"] == "ok"
    assert statuses["calibration"] == "stale"
