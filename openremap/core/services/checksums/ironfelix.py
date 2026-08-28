"""
IronFelix family checksum profiles — community-documented Bosch/Sagem
per-family schemes ported from the open-source IronFelix project
(nyetwurk/IronFelix, C++ modules).  One profile per family:

- ``vag_me7xx``   VAG Bosch ME7.XX (512 KB / 1 MB) — subtypes 1, 2, 3, 5, 6:
  1  old ME7.x: 3× CRC-32 (IEEE) over descriptor-driven zones
  2  old ME7.x: 3× CRC-32 over descriptor-driven zones
  3  new ME7.x: chained CRC-32 over 5 zones
  5  new ME7.x: chained CRC-32 over 5 zones + sum8 over 4 zones (v, ~v)
  6  new ME7.x: sum8 over 4 zones stored as (v, ~v)
  common: main tail sum (sum16le_acc32, shipped in checksum.py), the
  0x803C block checksum (per-8 KB-page first/last word sum), multipoint
- ``me3x``        Bosch M3.x-5.x (128/256 KB): sum8 from a page-marker
  table, stored as redundant BE16 triplets
- ``m797``        Hyundai Bosch M7.9.7 (512 KB): sum16le over 5
  descriptor-driven zones + multipoint
- ``m798``        Hyundai Bosch M7.9.8 (768/832 KB): 3 fixed zone sums +
  block checksum + multipoint
- ``china797``    China Bosch M7.9.7 (1 MB): 2 fixed zone sums + multipoint
- ``me745``       Citroen Bosch ME7.4.5 (832 KB): page-block checksum +
  multipoint + 3 CRC-32 checks
- ``samand``      Sagem Iran Khodro (832 KB): 2× sum8 stored LE16
- ``gs20``        Siemens GS20 TCU (64 KB data / 256 KB program):
  CRC-16/ARC over inclusive ranges, stored LE16 (MS4X community tool)
- ``smg2``        Siemens SMG II TCU (32 KB): CRC-16/ARC init 0x7878
  (MS4X community tool)

Detection is verify-only (no correction), faithful to the reference
implementation including its address encodings (C166 code splits 32-bit
addresses across non-contiguous byte positions).  Endianness note: the
reference project's ``SummInt16Intel`` = LE u16 words accumulated into a
u32 (our Rust algo id 11); ``SummInt8`` sums bytes; ``CalcCRC32`` is CRC-
32/IEEE (init 0xFFFFFFFF, final XOR 0xFFFFFFFF) with end-INCLUSIVE ranges.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openremap.core.arch.bytes_io import (
    crc32 as _crc32,
    crc32_cont as _crc32_cont,
    find_all as _find_all,
    sum16le as _sum16le,
    sum8 as _sum8,
    sumb_pages as _sumb_pages,
    u16be as _u16be,
    u16le as _u16le,
    u32le as _u32le,
)
from openremap.core.services.checksums.checksum import (
    detect_me7,
    detect_me7_multipoint,
    detect_me7_multipoint_unverified,
    verify_me7,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IronFelixCheck:
    """One checksum within a family profile."""

    name: str  # e.g. "crc32_zone1"
    status: str  # "ok" | "stale" | "absent"
    stored: int | None
    expected: int | None
    # Where the stored value lives in the file (store_offset, store_size
    # bytes) — recorded when the profile knows it.  Absent checks have no
    # store.  Consumed by the volatile-region classifier
    # (recipes/volatile.py) to exclude checksum stores from recipes.
    store_offset: int | None = None
    store_size: int = 0

    @property
    def stored_hex(self) -> str:
        return f"{self.stored:08X}" if self.stored is not None else ""

    @property
    def expected_hex(self) -> str:
        return f"{self.expected:08X}" if self.expected is not None else ""


@dataclass(frozen=True)
class IronFelixProfile:
    """Result of one family detector on one binary."""

    family: str  # registry key
    description: str
    subtype: int  # 0 when the family has no subtypes
    checks: tuple[IronFelixCheck, ...]
    multipoint_valid: int
    multipoint_unverified: int

    @property
    def ok(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def total(self) -> int:
        return len(self.checks)


# ---------------------------------------------------------------------------
# Result/bounds helpers
# ---------------------------------------------------------------------------


def _ok(
    name: str,
    stored: int,
    expected: int,
    store_offset: int | None = None,
    store_size: int = 0,
) -> IronFelixCheck:
    return IronFelixCheck(
        name,
        "ok" if stored == expected else "stale",
        stored,
        expected,
        store_offset,
        store_size,
    )


def _absent(name: str) -> IronFelixCheck:
    return IronFelixCheck(name, "absent", None, None)


def _in_range(n: int, s: int, e: int) -> bool:
    return 0 <= s < e <= n


# ---------------------------------------------------------------------------
# VAG Bosch ME7.XX (512 KB / 1 MB)
# ---------------------------------------------------------------------------

_EB_SIG1 = bytes.fromhex("2054C4500200F0C8F0D9D4E00200")
_EB_SIG2 = bytes.fromhex("0841F0582045C4400200F0C8F0D9D4E00200")
_EB_SIG3 = bytes.fromhex("88508840F0C8F0D9D4E00600DA")
_EB_SIG4 = bytes.fromhex("F0740871F0582075F2F4")
_CS_SIG6 = bytes.fromhex("E6F4FFFFE6F5FFFF")
_CS_SIG7 = bytes.fromhex("56F4FFFF56F5FFFF")
_CS_SIG1 = bytes.fromhex("F0545C2520545C2520545C45F085")
_CS_SIG2 = bytes.fromhex("F0545C2520545C2520545C45F0A5")
_REC_SIG1 = bytes.fromhex("00008000FFFB800000008200FFFF")
_REC_SIG2 = bytes.fromhex("F0545C2520545C2520545C45F0")
_REC_SIG3 = bytes.fromhex("F0740871F0582075F2F4")

_ME7_BASE = 0x800000


def _me7xx_subtype(data: bytes) -> int | None:
    if data.find(_EB_SIG1) != -1:
        return 1
    if data.find(_EB_SIG4) != -1:
        return 6
    eb2 = _find_all(data, _EB_SIG2)
    if len(eb2) < 3:
        return None
    if len(eb2) == 3:
        return 2
    return 5 if data.find(_EB_SIG3) != -1 else 3


def _me7xx_checks(data: bytes, subtype: int) -> list[IronFelixCheck] | None:
    """Port FindFixCRC's subtype switch.  Returns None when a required
    structure is missing (mirrors the reference returning 0)."""
    checks: list[IronFelixCheck] = []
    n = len(data)

    if subtype == 1:
        # 3 × CSummSig6 → 3 checksum store addresses (split-encoded)
        cs6 = _find_all(data, _CS_SIG6)
        if len(cs6) < 3:
            return None
        stores = []
        for off in cs6[:3]:
            addr = (
                _u16le(data, off + 0x22)
                + ((_u16le(data, off + 0x26)) << 16)
                - _ME7_BASE
            )
            stores.append(addr)
        # 3 × CSummSig1 → zone start/end addresses
        cs1 = _find_all(data, _CS_SIG1)
        if len(cs1) < 3:
            return None
        zones = []
        off = cs1[0]
        start = (data[off + 0x14] << 16) + (data[off + 0x15] << 24) - _ME7_BASE
        end = (
            _u16le(data, off + 0x38)
            + (data[off + 0x14] << 16)
            + (data[off + 0x15] << 24)
            - _ME7_BASE
            - 1
        )
        zones.append((start, end))
        for off in cs1[1:3]:
            start = _u16le(data, off + 0x12) + (data[off + 0x16] << 16) + (data[off + 0x17] << 24) - _ME7_BASE
            end = (
                _u16le(data, off + 0x3A)
                + (data[off + 0x16] << 16)
                + (data[off + 0x17] << 24)
                - _ME7_BASE
                - 1
            )
            zones.append((start, end))
        for i, ((s, e), store) in enumerate(zip(zones, stores), 1):
            if not _in_range(n, s, e) or not _in_range(n, store, store + 3):
                checks.append(_absent(f"crc32_zone{i}"))
                continue
            checks.append(_ok(f"crc32_zone{i}", _u32le(data, store), _crc32(data, s, e), store, 4))
        return checks

    if subtype == 2:
        cs6 = _find_all(data, _CS_SIG6)
        if len(cs6) < 3:
            return None
        stores = [
            _u16le(data, off + 0x22) + (_u16le(data, off + 0x26) << 16) - _ME7_BASE
            for off in cs6[:3]
        ]
        cs2 = _find_all(data, _CS_SIG2)
        if len(cs2) < 3:
            return None
        zones = []
        for off in cs2[:3]:
            start = _u16le(data, off - 0x0E) + (_u16le(data, off - 0x0A) << 16) - _ME7_BASE
            end = _u16le(data, off + 0x16) + (_u16le(data, off + 0x1A) << 16) - _ME7_BASE
            zones.append((start, end))
        for i, ((s, e), store) in enumerate(zip(zones, stores), 1):
            if not _in_range(n, s, e) or not _in_range(n, store, store + 3):
                checks.append(_absent(f"crc32_zone{i}"))
                continue
            checks.append(_ok(f"crc32_zone{i}", _u32le(data, store), _crc32(data, s, e), store, 4))
        return checks

    if subtype in (3, 5):
        # chained CRC-32 over 5 zones
        cs7 = _find_all(data, _CS_SIG7)
        if len(cs7) < 2:
            return None
        cs6 = _find_all(data, _CS_SIG6)
        if not cs6:
            return None
        store1 = _u16le(data, cs7[0] + 0x12) + (_u16le(data, cs7[0] + 0x16) << 16) - _ME7_BASE
        store2 = _u16le(data, cs7[1] + 0x12) + (_u16le(data, cs7[1] + 0x16) << 16) - _ME7_BASE
        store3 = _u16le(data, cs6[0] + 0x22) + (_u16le(data, cs6[0] + 0x26) << 16) - _ME7_BASE
        eb2 = _find_all(data, _EB_SIG2)
        if len(eb2) < 4:
            return None
        z2 = (data[eb2[0] - 0x4C] + (data[eb2[0] - 0x4B] << 8) + (data[eb2[0] - 0x48] << 16) + (data[eb2[0] - 0x47] << 24) - _ME7_BASE,
              data[eb2[0] - 0x28] + (data[eb2[0] - 0x27] << 8) + (data[eb2[0] - 0x24] << 16) + (data[eb2[0] - 0x23] << 24) - _ME7_BASE)
        z3 = (data[eb2[1] - 0x4C] + (data[eb2[1] - 0x4B] << 8) + (data[eb2[1] - 0x48] << 16) + (data[eb2[1] - 0x47] << 24) - _ME7_BASE,
              data[eb2[1] - 0x28] + (data[eb2[1] - 0x27] << 8) + (data[eb2[1] - 0x24] << 16) + (data[eb2[1] - 0x23] << 24) - _ME7_BASE)
        z4 = (data[eb2[2] - 0x50] + (data[eb2[2] - 0x4F] << 8) + (data[eb2[2] - 0x4C] << 16) + (data[eb2[2] - 0x4B] << 24) - _ME7_BASE,
              data[eb2[2] - 0x2C] + (data[eb2[2] - 0x2B] << 8) + (data[eb2[2] - 0x28] << 16) + (data[eb2[2] - 0x27] << 24) - _ME7_BASE)
        z5 = (data[eb2[3] - 0x4C] + (data[eb2[3] - 0x4B] << 8) + (data[eb2[3] - 0x48] << 16) + (data[eb2[3] - 0x47] << 24) - _ME7_BASE,
              data[eb2[3] - 0x28] + (data[eb2[3] - 0x27] << 8) + (data[eb2[3] - 0x24] << 16) + (data[eb2[3] - 0x23] << 24) - _ME7_BASE)
        z1_off = 0x2F4 if subtype == 3 else 0x448
        z1_len_off = z1_off + 7
        z1 = (data[eb2[3] + z1_off] + (data[eb2[3] + z1_off + 1] << 8) + (data[eb2[3] + z1_off + 4] << 16) + (data[eb2[3] + z1_off + 5] << 24) - _ME7_BASE,
              data[eb2[3] + z1_off] + (data[eb2[3] + z1_off + 1] << 8) + (data[eb2[3] + z1_off + 4] << 16) + (data[eb2[3] + z1_off + 5] << 24) - _ME7_BASE + ((data[eb2[3] + z1_len_off] >> 4) & 0xF) - 1)
        zones = [z1, z2, z3, z4, z5]
        if not all(_in_range(n, s, e) for s, e in zones):
            return None
        c1 = _crc32(data, *zones[0])
        c1 = _crc32_cont(data, *zones[1], c1)
        c2 = _crc32_cont(data, *zones[2], c1)
        c3 = _crc32_cont(data, *zones[3], c2)
        c3 = _crc32_cont(data, *zones[4], c3)
        for i, (store, expected) in enumerate(((store1, c1), (store2, c2), (store3, c3)), 1):
            if not _in_range(n, store, store + 3):
                checks.append(_absent(f"crc32_zone{i}"))
            else:
                checks.append(_ok(f"crc32_zone{i}", _u32le(data, store), expected, store, 4))
        if subtype == 5:
            # sum8 over zones 2-5 stored as (v, ~v)
            store4 = _u16le(data, cs6[0] + 0x1F0) + (_u16le(data, cs6[0] + 0x1F4) << 16) - _ME7_BASE
            total = sum(_sum8(data, s, e) for s, e in zones[1:]) & 0xFFFFFFFF
            if not _in_range(n, store4, store4 + 7):
                checks.append(_absent("sum8_zones"))
            else:
                stored, inv = struct.unpack_from("<2I", data, store4)
                if stored != (~inv & 0xFFFFFFFF):
                    checks.append(_absent("sum8_zones"))
                else:
                    checks.append(_ok("sum8_zones", stored, total, store4, 8))
        return checks

    if subtype == 6:
        # sum8 over 4 zones stored as (v, ~v)
        cs = _find_all(data, _EB_SIG4)
        if len(cs) < 4:
            return None
        store = _u16le(data, cs[0] + 0x242) + (_u16le(data, cs[0] + 0x246) << 16) - _ME7_BASE
        zones = [
            (data[cs[0] - 0x44] + (data[cs[0] - 0x43] << 8) + (data[cs[0] - 0x40] << 16) + (data[cs[0] - 0x3F] << 24) - _ME7_BASE,
             data[cs[0] - 0x24] + (data[cs[0] - 0x23] << 8) + (data[cs[0] - 0x20] << 16) + (data[cs[0] - 0x1F] << 24) - _ME7_BASE),
            (data[cs[1] - 0x44] + (data[cs[1] - 0x43] << 8) + (data[cs[1] - 0x40] << 16) + (data[cs[1] - 0x3F] << 24) - _ME7_BASE,
             data[cs[1] - 0x24] + (data[cs[1] - 0x23] << 8) + (data[cs[1] - 0x20] << 16) + (data[cs[1] - 0x1F] << 24) - _ME7_BASE),
            (data[cs[2] - 0x48] + (data[cs[2] - 0x47] << 8) + (data[cs[2] - 0x44] << 16) + (data[cs[2] - 0x43] << 24) - _ME7_BASE,
             data[cs[2] - 0x28] + (data[cs[2] - 0x27] << 8) + (data[cs[2] - 0x24] << 16) + (data[cs[2] - 0x23] << 24) - _ME7_BASE),
            (data[cs[3] - 0x44] + (data[cs[3] - 0x43] << 8) + (data[cs[3] - 0x40] << 16) + (data[cs[3] - 0x3F] << 24) - _ME7_BASE,
             data[cs[3] - 0x24] + (data[cs[3] - 0x23] << 8) + (data[cs[3] - 0x20] << 16) + (data[cs[3] - 0x1F] << 24) - _ME7_BASE),
        ]
        if not all(_in_range(n, s, e) for s, e in zones) or not _in_range(n, store, store + 7):
            return None
        stored, inv = struct.unpack_from("<2I", data, store)
        if stored != (~inv & 0xFFFFFFFF):
            return None
        total = sum(_sum8(data, s, e) for s, e in zones) & 0xFFFFFFFF
        checks.append(_ok("sum8_zones", stored, total, store, 8))
        return checks

    return None


def detect_me7xx(data: bytes) -> IronFelixProfile | None:
    """Port of module8.cpp (VAG Bosch ME7.XX)."""
    n = len(data)
    if n not in (0x80000, 0x100000):
        return None
    if not (
        data.find(_REC_SIG1) != -1
        or data.find(_REC_SIG2) != -1
        or data.find(_REC_SIG3) != -1
    ):
        return None
    subtype = _me7xx_subtype(data)
    if subtype is None:
        return None
    checks = _me7xx_checks(data, subtype)
    if checks is None:
        return None

    # common checks: 0x803C block checksum + main tail (reuses checksum.py)
    if _in_range(n, 0x8030, 0x8030 + 3) and _in_range(n, 0x8034, 0x8034 + 3):
        s1 = 0
        e1 = _u32le(data, 0x8030) - _ME7_BASE
        s2 = _u32le(data, 0x8034) + 2 - _ME7_BASE
        e2 = _u32le(data, 0x8014) - _ME7_BASE
        if _in_range(n, s1, e1) and _in_range(n, s2, e2):
            total = (_sumb_pages(data, s1, e1) + _sumb_pages(data, s2, e2)) & 0xFFFF
            checks.append(_ok("block_803c", _u16le(data, 0x803C), total, 0x803C, 2))
        else:
            checks.append(_absent("block_803c"))
    else:
        checks.append(_absent("block_803c"))

    me7 = detect_me7(data)
    verdict = verify_me7(data, me7)
    if verdict is None:
        checks.append(_absent("main_tail"))
    else:
        checks.append(
            IronFelixCheck(
                "main_tail",
                verdict.status,
                int(verdict.stored_hex, 16),
                int(verdict.expected_hex, 16),
                me7.stored_offset if me7 is not None else None,
                8 if me7 is not None else 0,
            )
        )

    mp_valid = detect_me7_multipoint(data)
    mp_unverified = detect_me7_multipoint_unverified(data)
    return IronFelixProfile(
        family="vag_me7xx",
        description="VAG Bosch ME7.XX",
        subtype=subtype,
        checks=tuple(checks),
        multipoint_valid=len(mp_valid),
        multipoint_unverified=len(mp_unverified),
    )


# ---------------------------------------------------------------------------
# Bosch M3.x-5.x (128 KB / 256 KB)
# ---------------------------------------------------------------------------


def _me3x_zone(data: bytes, store: int) -> tuple[int, int] | None:
    """One of the two zones at 0xBF00 / 0xDF00.  Returns (sum_store,
    aux_store) with the zone end, or None when the pair is invalid."""
    if store + 6 > len(data):
        return None
    a = _u16be(data, store)
    b = _u16be(data, store + 2)
    c = _u16be(data, store + 4)
    if a != (b + c) & 0xFFFF:
        return None
    return (store + 4, store)  # primary BE16 store, aux BE16 store


def detect_me3x(data: bytes) -> IronFelixProfile | None:
    """Port of module2.cpp (Bosch M3.x-5.x)."""
    n = len(data)
    if n not in (0x20000, 0x40000):
        return None
    zone = _me3x_zone(data, 0xBF00)
    if zone is not None:
        end = 0xBEFF
    else:
        zone = _me3x_zone(data, 0xDF00)
        if zone is None:
            return None
        end = 0xDEFF
    if end >= n:
        return None
    # page-marker table: first address where LE16(data[addr]) == addr
    # (reference reads *(B+addr) + (*(B+addr+1)<<8) — little-endian,
    # unlike the BE16 recognition triplets)
    start = None
    for addr in range(0x4000, end, 0x80):
        if _u16le(data, addr) == addr:
            start = addr
            break
    if start is None:
        return None
    calc = _sum8(data, start, end) & 0xFFFF
    store1, store2 = zone
    stored1 = _u16be(data, store1)
    stored2_aux = _u16be(data, store2 + 2)
    checks = [
        _ok("sum8_primary", stored1, calc, store1, 2),
        # reference's second check is redundant by construction — kept
        # for fidelity: (aux + primary) == (calc + aux)  (mod 2^16)
        _ok(
            "sum8_redundant",
            (stored2_aux + stored1) & 0xFFFF,
            (calc + stored2_aux) & 0xFFFF,
            store2 + 2,
            2,
        ),
    ]
    return IronFelixProfile(
        family="me3x",
        description="Bosch M3.x-5.x",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=0,
        multipoint_unverified=0,
    )


# ---------------------------------------------------------------------------
# Hyundai Bosch M7.9.7 (512 KB)
# ---------------------------------------------------------------------------

_M797_SIG1 = bytes.fromhex("00008000FD5E800000808000FFFB8000")
_M797_SIG2 = bytes.fromhex("00008000FF5F800000808000FFFB8000")


def detect_m797(data: bytes) -> IronFelixProfile | None:
    """Port of module4.cpp (Hyundai Bosch M7.9.7)."""
    n = len(data)
    if n != 0x80000:
        return None
    off = data.find(_M797_SIG1)
    if off == -1:
        off = data.find(_M797_SIG2)
        if off == -1:
            return None
    # 5 × 8-byte descriptor entries: (start, end) u32 LE pairs
    total = 0
    checks: list[IronFelixCheck] = []
    for i in range(5):
        start = _u32le(data, off + 8 * i) - _ME7_BASE
        end = _u32le(data, off + 8 * i + 4) - _ME7_BASE
        if not _in_range(n, start, end):
            return None
        total = (total + _sum16le(data, start, end)) & 0xFFFFFFFF
    store = 0x07FFE0
    stored, inv = struct.unpack_from("<2I", data, store)
    if stored != (~inv & 0xFFFFFFFF):
        checks.append(_absent("main_tail"))
    else:
        checks.append(_ok("main_tail", stored, total, store, 8))
    return IronFelixProfile(
        family="m797",
        description="Hyundai Bosch M7.9.7",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=len(detect_me7_multipoint(data)),
        multipoint_unverified=len(detect_me7_multipoint_unverified(data)),
    )


# ---------------------------------------------------------------------------
# Hyundai Bosch M7.9.8 (768 KB / 832 KB)
# ---------------------------------------------------------------------------

_M798_SIG = bytes.fromhex("F0EAF0FBE6FCF5FFE09D0D13DC09")
_M798_CS = bytes.fromhex("DC0DA88CE00900E810F9F08CF01D06F8")


def _m798_shift(addr: int, size: int) -> int:
    """Address correction for 768 KB files (reference module3.cpp)."""
    if size != 0xC0000:
        return addr
    if 0x18000 <= addr < 0xA0000:
        return addr - 0x10000
    if 0xC0000 <= addr < 0xCFFFF:
        return addr - 0x30000
    return addr


def detect_m798(data: bytes) -> IronFelixProfile | None:
    """Port of module3.cpp (Hyundai Bosch M7.9.8)."""
    n = len(data)
    if n not in (0xD0000, 0xC0000):
        return None
    if data.find(_M798_SIG) == -1:
        return None
    checks: list[IronFelixCheck] = []

    # block checksum (signature-driven, 32-bit SummBlock over 2 zones)
    off = data.find(_M798_CS)
    if off != -1:
        s1 = _u32le(data, off - 0x08)
        e1 = _u32le(data, off + 0x26)
        if n == 0xC0000 and 0x18000 <= s1 < 0xA0000:
            s1 -= 0x10000
            e1 -= 0x10000
        elif n == 0xC0000 and 0xC0000 <= s1 < 0xCFFFF:
            s1 -= 0x30000
            e1 -= 0x30000
        off2 = data.find(_M798_CS, off + 16)
        if off2 != -1:
            s2 = _u32le(data, off2 - 0x08)
            e2 = _u32le(data, off2 + 0x26)
            store = _u16le(data, off2 + 0x3C) + (data[off2 + 0x38] << 14) + (data[off2 + 0x39] << 22)
            if n == 0xC0000 and 0x18000 <= s2 < 0xA0000:
                s2 -= 0x10000
                e2 -= 0x10000
            elif n == 0xC0000 and 0xC0000 <= s2 < 0xCFFFF:
                s2 -= 0x30000
                e2 -= 0x30000
            if n == 0xC0000 and 0x18000 <= store < 0xA0000:
                store -= 0x10000
            elif n == 0xC0000 and 0xC0000 <= store < 0xCFFFF:
                store -= 0x30000
            if _in_range(n, s1, e1) and _in_range(n, s2, e2) and _in_range(n, store, store + 3):
                total = (_sumb_pages(data, s1, e1) + _sumb_pages(data, s2, e2)) & 0xFFFFFFFF
                checks.append(_ok("block_sig", _u32le(data, store), total, store, 4))
            else:
                checks.append(_absent("block_sig"))
        else:
            checks.append(_absent("block_sig"))
    else:
        checks.append(_absent("block_sig"))

    # three fixed zone sums stored as (v, ~v) pairs
    zones = [
        (0x18000, 0x9FFF5, 0x9FFF6),
        (0xA0000, 0xBFFF5, 0xBFFF6),
        (0xC0000, 0xCFFF5, 0xCFFF6),
    ]
    for i, (s, e, store) in enumerate(zones, 1):
        if n == 0xC0000:
            s = _m798_shift(s, n)
            e = _m798_shift(e, n)
            store = _m798_shift(store, n)
        stored, inv = struct.unpack_from("<2I", data, store)
        if stored != (~inv & 0xFFFFFFFF):
            checks.append(_absent(f"zone{i}"))
        else:
            checks.append(_ok(f"zone{i}", stored, _sum16le(data, s, e), store, 8))

    # multipoint descriptors at 0xBBBDE..0xBBEDE (fixed range, base 0)
    valid = 0
    unverified = 0
    for mcs in range(0xBBBDE, 0xBBEDE, 0x10):
        start = _u32le(data, mcs)
        end = _u32le(data, mcs + 4)
        start = _m798_shift(start, n)
        end = _m798_shift(end, n)
        if end <= n and start < end:
            stored, inv = struct.unpack_from("<2I", data, mcs + 8)
            if stored != (~inv & 0xFFFFFFFF):
                unverified += 1
                continue
            if _sum16le(data, start, end) == stored:
                valid += 1
            else:
                unverified += 1
    return IronFelixProfile(
        family="m798",
        description="Hyundai Bosch M7.9.8",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=valid,
        multipoint_unverified=unverified,
    )


# ---------------------------------------------------------------------------
# China Bosch M7.9.7 (1 MB)
# ---------------------------------------------------------------------------

_CHINA_SIG = bytes.fromhex("00008000FFFF800000008100FFFF8F00")


def detect_china797(data: bytes) -> IronFelixProfile | None:
    """Port of module5.cpp (China Bosch M7.9.7)."""
    n = len(data)
    if n != 0x100000:
        return None
    if data.find(_CHINA_SIG) == -1:
        return None
    checks: list[IronFelixCheck] = []
    total = (_sum16le(data, 0x0000, 0xFFFF) + _sum16le(data, 0x10000, 0xFFFFF)) & 0xFFFFFFFF
    store = 0xFFFE8
    stored, inv = struct.unpack_from("<2I", data, store)
    if stored != (~inv & 0xFFFFFFFF):
        checks.append(_absent("main_tail"))
    else:
        checks.append(_ok("main_tail", stored, total, store, 8))
    return IronFelixProfile(
        family="china797",
        description="China Bosch M7.9.7",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=len(detect_me7_multipoint(data)),
        multipoint_unverified=len(detect_me7_multipoint_unverified(data)),
    )


# ---------------------------------------------------------------------------
# Citroen Bosch ME7.4.5 (832 KB)
# ---------------------------------------------------------------------------

_ME745_SIG1 = bytes.fromhex("88E088D0E6FC0020E09DE6FEFFFFE0AF")
_ME745_SIG2 = bytes.fromhex("88508840E6FC0020E09DE6FEFFFFE0AF")
_ME745_CS1 = bytes.fromhex("00000000FF3F0000")
_ME745_CS2 = bytes.fromhex("00C00C00D3FF0C00")


def detect_me745(data: bytes) -> IronFelixProfile | None:
    """Port of module6.cpp (Citroen Bosch ME7.4.5)."""
    n = len(data)
    if n != 0xD0000:
        return None
    if data.find(_ME745_SIG1) == -1 and data.find(_ME745_SIG2) == -1:
        return None
    cs_off = data.find(_ME745_CS1)
    shifted = cs_off != -1 and cs_off < 0xC0000
    checks: list[IronFelixCheck] = []

    # page-block checksum (SummBlock over 3 zones, direct u32 store)
    zones = [(0x02000, 0x07FFF), (0x20000, 0x8FFFF), (0x92000, 0xAFFFF)]
    store = 0xB7FFA
    if shifted:
        zones = [(0x02000, 0x07FFF), (0x10000, 0x7FFFF), (0x82000, 0x9FFFF)]
        store = 0xA7FFA
    total = 0
    for s, e in zones:
        total = (total + _sumb_pages(data, s, e)) & 0xFFFFFFFF
    checks.append(_ok("block_pages", _u32le(data, store), total, store, 4))

    # multipoint descriptors (base 0, -0x10000 correction when shifted)
    valid = 0
    unverified = 0
    start_scan = data.find(_ME745_CS1)
    if start_scan != -1:
        end_scan = data.find(_ME745_CS2, start_scan + 0x01E0)
        if end_scan != -1:
            for mcs in range(start_scan, end_scan + 0x10, 0x10):
                start = _u32le(data, mcs)
                end = _u32le(data, mcs + 4)
                if shifted and start >= 0x18000:
                    start -= 0x10000
                    end -= 0x10000
                if end <= n and start < end:
                    stored, inv = struct.unpack_from("<2I", data, mcs + 8)
                    if stored != (~inv & 0xFFFFFFFF):
                        unverified += 1
                        continue
                    if _sum16le(data, start, end) == stored:
                        valid += 1
                    else:
                        unverified += 1

    # CRC-32 #1: three chained zones → 0xCFFD8
    z = [(0x00000, 0x07FFF), (0x20000, 0x8FFFF), (0x92000, 0xAFFFF)]
    store = 0xCFFD8
    if shifted:
        z = [(0x00000, 0x07FFF), (0x10000, 0x7FFFF), (0x82000, 0x9FFFF)]
        store = 0xBFFD8
    crc = _crc32(data, *z[0])
    crc = _crc32_cont(data, *z[1], crc)
    crc = _crc32_cont(data, *z[2], crc)
    checks.append(_ok("crc32_main", _u32le(data, store), crc, store, 4))

    # CRC-32 #2: boot block → 0x1FFFC / 0xFFFC
    s, e = (0x18000, 0x1F9CF)
    store = 0x1FFFC
    if shifted:
        s, e, store = 0x8000, 0xF9CF, 0xFFFC
    checks.append(_ok("crc32_boot", _u32le(data, store), _crc32(data, s, e), store, 4))

    # CRC-32 #3: trailing block → 0xCFFD4 / 0xBFFD4
    s, e = (0xB0000, 0xCFFD3)
    store = 0xCFFD4
    if shifted:
        s, e, store = 0xA0000, 0xBFFD3, 0xBFFD4
    checks.append(_ok("crc32_tail", _u32le(data, store), _crc32(data, s, e), store, 4))

    return IronFelixProfile(
        family="me745",
        description="Citroen Bosch ME7.4.5",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=valid,
        multipoint_unverified=unverified,
    )


# ---------------------------------------------------------------------------
# Sagem Iran Khodro (832 KB)
# ---------------------------------------------------------------------------

_SAMAND_SIG = bytes.fromhex("DC06A84108121860C0850035C0950035")


def detect_samand(data: bytes) -> IronFelixProfile | None:
    """Port of module1.cpp (Sagem Iran Khodro)."""
    n = len(data)
    if n != 0xD0000:
        return None
    if data.find(_SAMAND_SIG) == -1:
        return None
    checks: list[IronFelixCheck] = []
    calc1 = (
        _sum8(data, 0x02000, 0x03FFF) + _sum8(data, 0x10000, 0x6FFFD)
    ) & 0xFFFF
    calc2 = _sum8(data, 0x72806, 0x7FFFF) & 0xFFFF
    checks.append(_ok("sum8_main", _u16le(data, 0x6FFFE), calc1, 0x6FFFE, 2))
    checks.append(_ok("sum8_tail", _u16le(data, 0x72804), calc2, 0x72804, 2))
    return IronFelixProfile(
        family="samand",
        description="Sagem Iran Khodro",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=0,
        multipoint_unverified=0,
    )


# ---------------------------------------------------------------------------
# Siemens GS20 TCU / SMG II (32 KB / 64 KB / 256 KB)
#
# Community algorithm (MS4X wiki, "Siemens TCU GS20 SMGII Checksum
# Corrector" tool — decompiled): CRC-16/ARC (poly 0x8005 reflected) with
# per-variant init/region/store:
#   GS20 64 KB data:    init 0x0000, region [0x2A, 0xFFC7] incl, store LE16 @0x0D
#   GS20 256 KB code:   init 0x0000, regions [0, 511] + [640, 261631] incl, store @261836
#   SMG2 32 KB data:    init 0x7878, region [8416, 30911] incl, store @8320
# ---------------------------------------------------------------------------

_GS20_RANGES_DATA = ((0x2A, 0xFFC7),)
_GS20_STORE_DATA = 0x0D
_GS20_RANGES_CODE = ((0x0000, 0x01FF), (0x0280, 0x3FDFF))
_GS20_STORE_CODE = 261836
_SMG2_RANGES = ((8416, 30911),)
_SMG2_INIT = 0x7878
_SMG2_STORE = 8320


def _crc16_arc_gs20(data: bytes, ranges: tuple[tuple[int, int], ...], init: int) -> int:
    """CRC-16/ARC as used by the community GS20/SMG2 corrector: reflected
    poly 0x8005, inclusive byte ranges."""
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
        table.append(c & 0xFFFF)
    crc = init & 0xFFFF
    for s, e in ranges:
        for i in range(s, e + 1):
            crc = (table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8)) & 0xFFFF
    return crc


def _detect_gs20_variant(
    data: bytes, variant: str, ranges: tuple[tuple[int, int], ...],
    init: int, store: int,
) -> IronFelixProfile:
    checks: list[IronFelixCheck] = []
    stored = _u16le(data, store)
    checks.append(_ok("crc16_arc", stored, _crc16_arc_gs20(data, ranges, init), store, 2))
    return IronFelixProfile(
        family="gs20",
        description=f"Siemens GS20 TCU ({variant})",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=0,
        multipoint_unverified=0,
    )


def detect_gs20(data: bytes) -> IronFelixProfile | None:
    """Port of the community GS20/SMG2 checksum corrector (MS4X wiki)."""
    n = len(data)
    if n == 65536:
        return _detect_gs20_variant(data, "64 KB data", _GS20_RANGES_DATA, 0x0000, _GS20_STORE_DATA)
    if n == 262144:
        return _detect_gs20_variant(data, "256 KB program", _GS20_RANGES_CODE, 0x0000, _GS20_STORE_CODE)
    return None


def detect_smg2(data: bytes) -> IronFelixProfile | None:
    """Port of the community GS20/SMG2 checksum corrector (MS4X wiki)."""
    n = len(data)
    if n != 32768:
        return None
    checks: list[IronFelixCheck] = []
    stored = _u16le(data, _SMG2_STORE)
    checks.append(_ok("crc16_arc", stored, _crc16_arc_gs20(data, _SMG2_RANGES, _SMG2_INIT), _SMG2_STORE, 2))
    return IronFelixProfile(
        family="smg2",
        description="Siemens SMG II TCU (32 KB data)",
        subtype=0,
        checks=tuple(checks),
        multipoint_valid=0,
        multipoint_unverified=0,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

IRONFELIX_FAMILIES: dict[str, str] = {
    "vag_me7xx": "VAG Bosch ME7.XX",
    "me3x": "Bosch M3.x-5.x",
    "m797": "Hyundai Bosch M7.9.7",
    "m798": "Hyundai Bosch M7.9.8",
    "china797": "China Bosch M7.9.7",
    "me745": "Citroen Bosch ME7.4.5",
    "samand": "Sagem Iran Khodro",
    "gs20": "Siemens GS20 TCU",
    "smg2": "Siemens SMG II TCU",
}

_DETECTORS = {
    "vag_me7xx": detect_me7xx,
    "me3x": detect_me3x,
    "m797": detect_m797,
    "m798": detect_m798,
    "china797": detect_china797,
    "me745": detect_me745,
    "samand": detect_samand,
    "gs20": detect_gs20,
    "smg2": detect_smg2,
}


def detect_all(data: bytes) -> list[IronFelixProfile]:
    """Run every family detector; return profiles in registry order."""
    out: list[IronFelixProfile] = []
    for family in IRONFELIX_FAMILIES:
        try:
            profile = _DETECTORS[family](data)
        except (IndexError, struct.error, ValueError):
            profile = None
        if profile is not None:
            out.append(profile)
    return out
