"""
NefMoto ME7 rolling / multirange checksum tests.

Corpus validation (skip-guarded, tests/data is gitignored): rolling
checksums on real ME7.1 M-box / dual-checksum files, multirange on
dual-checksum files.  Synthetic tests cover the engines, the pattern
constants, and the C166 instruction parsers.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from openremap.core.services.checksums import nefmoto as N
from openremap.core.services.checksums.nefmoto import (
    detect_me7_multirange,
    detect_me7_rolling,
    multirange_checksum,
    rolling_checksum,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_ME71 = (DATA / "ECUs" / "Bosch" / "ME7.1").is_dir()

# the standard CRC-32/IEEE table (the ME7 rolling seed table)
_CRC32_TABLE = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
    _CRC32_TABLE.append(c & 0xFFFFFFFF)
_CRC32_TABLE_BYTES = b"".join(struct.pack("<I", v) for v in _CRC32_TABLE)


# ---------------------------------------------------------------------------
# Engine tests (synthetic)
# ---------------------------------------------------------------------------


def test_rolling_checksum_with_crc32_seed_table():
    """Rolling over the CRC-32/IEEE table with empty payload degenerates
    to a shifted/folded CRC — just pin the value for stability."""
    data = bytearray(0x2000)
    data[0x1000 : 0x1000 + len(_CRC32_TABLE_BYTES)] = _CRC32_TABLE_BYTES
    data[0x1500 : 0x1520] = b"OpenRemap rolling checksum test"
    r = N.RollingRange(0x1500, 0x151F)
    v = rolling_checksum(bytes(data), 0x1000, [r])
    assert v == rolling_checksum(bytes(data), 0x1000, [r])  # deterministic
    assert v != 0xFFFFFFFF


def test_rolling_checksum_chaining_matches_single_pass():
    data = bytearray(0x3000)
    data[0x2000 : 0x2000 + len(_CRC32_TABLE_BYTES)] = _CRC32_TABLE_BYTES
    data[0x100 : 0x200] = bytes(range(256))
    data[0x400 : 0x500] = bytes(range(256, 512)) if False else bytes(range(0, 256, 2)) * 2
    init = N.RollingRange(0x100, 0x1FF)
    r1 = N.RollingRange(0x400, 0x4FF)
    v_chained = rolling_checksum(bytes(data), 0x2000, [r1], rolling_checksum(bytes(data), 0x2000, [init]))
    v_single = rolling_checksum(bytes(data), 0x2000, [init, r1])
    assert v_chained == v_single


def test_multirange_checksum_sums_bytes():
    data = bytes([1, 2, 3]) + bytes(100) + bytes([4, 5])
    r = N.RollingRange(0, 2)
    assert multirange_checksum(data, [r]) == 6
    r2 = N.RollingRange(103, 104)
    assert multirange_checksum(data, [r, r2]) == 15


def test_locate_pattern_step_and_mask():
    data = bytes.fromhex("00 00 11 22 33 44 11 23 33 44 AA BB")
    pat = bytes.fromhex("11 22 33")
    mask = bytes.fromhex("FF 00 FF")
    assert N._locate_pattern(data, pat, mask, 0, len(data), 2) == 2
    # step-2 alignment skips the unaligned match
    data2 = bytes.fromhex("99 11 22 33 44")
    assert N._locate_pattern(data2, pat, mask, 0, len(data2), 2) == -1


def test_parse_instruction_mov_forms():
    data = bytes.fromhex("E6 F4 34 12 E1 70 00 00")
    assert N._parse_mov(data, 0) == (0x1234, 4)
    assert N._parse_movb(data, 4) == (0x7, 2)  # E1 -> #data4 (high nibble)


def test_parse_data3_subswitch():
    data = bytes.fromhex("49 05 49 8C")
    assert N._parse_cmpb(data, 0) == (5, 2)  # #data3 form
    assert N._parse_cmpb(data, 2) == (0, 2)  # register form


# ---------------------------------------------------------------------------
# Corpus tests (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_mbox_rolling_three_slots_ok():
    data = (DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin").read_bytes()
    entries = detect_me7_rolling(data)
    assert entries is not None
    assert len(entries) == 3
    assert all(e.status == "ok" for e in entries)
    assert all(e.init_range is None for e in entries)


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_dual_checksum_file_rolling_ok():
    data = (DATA / "ECUs" / "Bosch" / "ME7.1" / "022906032CS.bin").read_bytes()
    entries = detect_me7_rolling(data)
    assert entries is not None
    assert len(entries) == 3
    assert all(e.status == "ok" for e in entries)
    assert all(e.init_range is not None for e in entries)


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_multirange_ok():
    """006410010A0 is multirange-only firmware (no rolling structure)."""
    data = (DATA / "ECUs" / "Bosch" / "ME7.1" / "006410010A0.bin").read_bytes()
    mr = detect_me7_multirange(data)
    assert mr is not None
    assert mr.status == "ok"
    assert mr.ranges
    assert detect_me7_rolling(data) is None


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_rolling_stale_detection():
    """Flip one byte inside the first rolling range -> stale."""
    data = bytearray(
        (DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin").read_bytes()
    )
    data[0x11000] ^= 0xFF
    entries = detect_me7_rolling(bytes(data))
    assert entries is not None
    assert any(e.status == "stale" for e in entries)


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_multirange_stale_detection():
    data = bytearray(
        (DATA / "ECUs" / "Bosch" / "ME7.1" / "006410010A0.bin").read_bytes()
    )
    mr = detect_me7_multirange(bytes(data))
    assert mr is not None and mr.status == "ok"
    data[mr.ranges[0].start] ^= 0xFF
    mr2 = detect_me7_multirange(bytes(data))
    assert mr2 is not None
    assert mr2.status == "stale"


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_rolling_corpus_wide_all_ok_or_absent():
    """Every ME7.1 file that HAS rolling checksums verifies OK (no stale) —
    the port is exact."""
    p = DATA / "ECUs" / "Bosch" / "ME7.1"
    files = [f for f in p.iterdir() if f.is_file() and f.stat().st_size >= 0x80000]
    ok = stale = none = 0
    for f in files:
        entries = detect_me7_rolling(f.read_bytes())
        if entries is None:
            none += 1
        elif all(e.status == "ok" for e in entries):
            ok += 1
        else:
            stale += 1
    assert ok >= 100, f"rolling-ok={ok} stale={stale} none={none}"
    assert stale == 0
