"""
core/arch/bytes_io — shared byte readers (golden-value parity).

These helpers moved verbatim from the checksum modules (ironfelix.py /
ms43.py / nefmoto.py) in the Phase 1 arch-domain scaffold.  Their
correctness is exercised end-to-end by the ironfelix/ms43/nefmoto
detector suites; this file pins the standalone values so a future edit
to bytes_io cannot silently drift either family of readers.

Two families on purpose (do not merge): non-nullable readers raise on
out-of-bounds (ironfelix/nefmoto callers pre-check or guarantee
offsets); nullable readers return ``None`` (ms43 treats OOB as
"absent").
"""

from __future__ import annotations

from openremap.core.arch.bytes_io import (
    crc32,
    crc32_cont,
    find_all,
    sum16le,
    sum8,
    sumb_pages,
    u16be,
    u16be_opt,
    u16le,
    u16le_opt,
    u32le,
    u32le_opt,
)


def test_nonnullable_readers():
    data = bytes(range(16))  # 0x00..0x0F
    assert u16le(data, 0) == 0x0100
    assert u16be(data, 0) == 0x0001
    assert u32le(data, 0) == 0x03020100
    assert u16le(data, 8) == 0x0908
    assert u16be(data, 8) == 0x0809
    assert u32le(data, 8) == 0x0B0A0908


def test_nullable_readers_are_none_out_of_bounds():
    data = bytes(16)
    assert u16le_opt(data, 15) is None  # needs 2 bytes
    assert u16be_opt(data, 15) is None
    assert u32le_opt(data, 15) is None  # needs 4 bytes
    assert u32le_opt(data, 13) is None
    assert u16le_opt(data, -1) is None
    assert u32le_opt(data, -1) is None
    # in-bounds still reads correctly
    assert u16le_opt(data, 0) == 0
    assert u16be_opt(data, 0) == 0
    assert u32le_opt(data, 0) == 0


def test_crc32_standard_check_value_and_continuation():
    # CRC-32/IEEE (init 0xFFFFFFFF, final XOR 0xFFFFFFFF) — the standard
    # "123456789" check value.
    assert crc32(b"123456789", 0, 8) == 0xCBF43926
    # chained continuation equals a single run (the IronFelix CalcCRC32Cont
    # semantics: continue from the previous result)
    chained = crc32(b"1234", 0, 3)
    chained = crc32_cont(b"56789", 0, 4, chained)
    assert chained == 0xCBF43926


def test_range_sums():
    data = bytes(range(256))
    assert sum8(data, 0, 9) == 45
    assert sum8(data, 0, 9) == sum(range(10))
    # sum16le: LE u16 words at even offsets, u32 accumulator
    assert sum16le(b"\x01\x02\x03\x04", 0, 4) == 0x0201 + 0x0403
    # sumb_pages: first + last u16 of each 0x2000 page
    pages = bytes(i & 0xFF for i in range(0x2000))
    assert sumb_pages(pages, 0, 0x2000) == 0x0100 + 0xFFFE


def test_find_all():
    data = b"abXabXab"
    assert find_all(data, b"ab") == [0, 3, 6]
    assert find_all(data, b"X") == [2, 5]
    assert find_all(data, b"zz") == []
    assert find_all(data, b"ab", start=4) == [6]
