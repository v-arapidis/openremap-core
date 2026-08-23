"""
Denso Subaru checksum tests (SH72531 1 MB descriptor table).

Corpus validation (skip-guarded): the CAN-era 1 MB factory ROMs carry a
descriptor table that verifies fully; flipping one byte in a covered
region makes the covering entries stale.  Synthetic: a builder that
produces a fully verifying Denso-style file plus corruption and
recognition tests.  Cross-check: no false positives on Bosch/Siemens
corpus samples.
"""

from __future__ import annotations

import os
import random
import struct
from pathlib import Path

import pytest

from openremap.core.services.checksums.denso import (
    CHECK_TOTAL,
    DensoChecksumInfo,
    detect_denso,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

_TABLE = 0xFFB80
_ENTRIES = 12


def _u32be(data: bytes | bytearray, i: int) -> int:
    return struct.unpack_from(">I", data, i)[0]


def build_denso(seed: int = 1, size: int = 0x100000, table: int = _TABLE,
                entries: int = _ENTRIES, tail: bool = True) -> bytes:
    """A 1 MB Denso-style file whose descriptor table fully verifies.

    Entry k covers [0x4000 + k*0x800, 0x4000 + k*0x800 + 0x7FF]
    (end-inclusive → effective end +1, 4-aligned).
    """
    rng = random.Random(seed)
    data = bytearray(rng.randbytes(size))
    for k in range(entries):
        s = 0x4000 + k * 0x800
        e = s + 0x7FF
        total = 0
        for j in range(s, e + 1, 4):
            total = (total + _u32be(data, j)) & 0xFFFFFFFF
        diff = (CHECK_TOTAL - total) & 0xFFFFFFFF
        struct.pack_into(">III", data, table + k * 12, s, e, diff)
    if tail:
        # trailing disabled entries, as real tables have
        for k in range(entries, entries + 5):
            struct.pack_into(">III", data, table + k * 12, 0, 0, CHECK_TOTAL)
    return bytes(data)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_finds_and_verifies(self):
        data = build_denso()
        r = detect_denso(data)
        assert r is not None
        assert r.table_offset == _TABLE
        assert r.status == "ok"
        assert r.ok == _ENTRIES
        assert r.total == _ENTRIES
        assert len(r.entries) == _ENTRIES + 5  # trailing disabled reported

    def test_entries_report_disabled(self):
        r = detect_denso(build_denso())
        assert r is not None
        assert all(e.status == "disabled" for e in r.entries[_ENTRIES:])

    def test_none_on_small_files(self):
        assert detect_denso(os.urandom(0x2000)) is None

    def test_none_on_random_1mb(self):
        assert detect_denso(os.urandom(0x100000)) is None

    def test_shifted_table_detected(self):
        # shifted dump: file is 1 MB + delta, table at unaligned offset
        delta = 0x9B
        data = build_denso(size=0x100000 + delta, table=_TABLE + delta)
        r = detect_denso(data)
        assert r is not None
        assert r.table_offset == _TABLE + delta

    def test_no_tail_table_detected(self):
        r = detect_denso(build_denso(tail=False))
        assert r is not None
        assert len(r.entries) == _ENTRIES

    def test_short_table_rejected(self):
        data = build_denso(entries=2)
        assert detect_denso(data) is None


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------


class TestStale:
    def test_flipped_byte_makes_covering_entry_stale(self):
        data = bytearray(build_denso())
        data[0x4600] ^= 0xFF  # inside entry 0's region [0x4000, 0x47FF]
        r = detect_denso(bytes(data))
        assert r is not None
        assert r.status == "stale"
        assert r.entries[0].status == "stale"
        assert r.entries[1].status == "ok"
        assert r.entries[0].expected != r.entries[0].stored

    def test_flip_in_uncovered_area_keeps_ok(self):
        data = bytearray(build_denso())
        data[0x3000] ^= 0xFF  # below entry 0's region start (0x4000)
        r = detect_denso(bytes(data))
        assert r is not None
        assert r.status == "ok"

    def test_flip_in_table_area_keeps_ok(self):
        data = bytearray(build_denso())
        data[_TABLE + 4] ^= 0xFF  # inside a descriptor word, outside regions
        r = detect_denso(bytes(data))
        assert r is None or r.status == "ok"


# ---------------------------------------------------------------------------
# Corpus (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SUBARU, reason="tests/data/ECUs/Subaru corpus missing")
class TestCorpus:
    def test_factory_can_files_verify(self):
        f = DATA / "ECUs" / "Subaru" / "ADM" / "Forester" / "AZ1G100T-2009-SADM-Subaru-Forester-XT-AT.hex"
        r = detect_denso(f.read_bytes())
        assert r is not None
        assert r.status == "ok"
        assert r.ok >= 12

    def test_flip_makes_corpus_file_stale(self):
        f = DATA / "ECUs" / "Subaru" / "ADM" / "Forester" / "AZ1G100T-2009-SADM-Subaru-Forester-XT-AT.hex"
        data = bytearray(f.read_bytes())
        data[0x8000] ^= 0xFF
        r = detect_denso(bytes(data))
        assert r is not None
        assert r.status == "stale"

    def test_16bit_has_no_table(self):
        f = DATA / "ECUs" / "Subaru" / "ADM" / "Impreza" / "A4RG060P-2001-02-ADM-Subaru-Impreza-STi.hex"
        assert detect_denso(f.read_bytes()) is None

    def test_no_false_positives_on_bosch(self):
        seen = 0
        for f in (DATA / "ECUs" / "Bosch").rglob("*"):
            if not f.is_file() or f.stat().st_size > 0x200000:
                continue
            assert detect_denso(f.read_bytes()) is None, f.name
            seen += 1
            if seen >= 40:
                break
        assert seen > 0

    def test_no_false_positives_on_siemens(self):
        seen = 0
        for f in (DATA / "ECUs" / "Siemens").rglob("*"):
            if not f.is_file() or f.stat().st_size > 0x200000:
                continue
            assert detect_denso(f.read_bytes()) is None, f.name
            seen += 1
        assert seen > 0
