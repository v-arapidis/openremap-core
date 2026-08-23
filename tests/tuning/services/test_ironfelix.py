"""
IronFelix family profile tests.

Real-corpus validation (skip-guarded, tests/data is gitignored):
- vag_me7xx on the ME7.1 / ME7.1.1 / ME7.5 / ME71 corpus
- me3x on the M3.8 corpus

Synthetic validation for every family: build a structurally valid binary
whose checksums verify, assert all checks OK, corrupt one covered byte,
assert STALE.  The remaining families (m797/m798/china797/me745/samand)
have no corpus files, so the synthetic fixtures are their ground truth.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from openremap.core.services.checksums.ironfelix import (
    IRONFELIX_FAMILIES,
    _ME7_BASE,
    detect_all,
    detect_me3x,
    detect_me745,
    detect_me7xx,
    detect_m797,
    detect_m798,
    detect_china797,
    detect_samand,
    detect_gs20,
    detect_smg2,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"

HAS_ME71 = (DATA / "ECUs" / "Bosch" / "ME7.1").is_dir()
HAS_M38 = (DATA / "ECUs" / "Bosch" / "M3.8").is_dir()


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------


def _scrub(data: bytearray, patterns: tuple[bytes, ...], keep: tuple[int, ...]) -> None:
    """Neutralize accidental occurrences of signature patterns outside the
    deliberately placed ones (first byte zeroed)."""
    for pat in patterns:
        start = 0
        while True:
            off = data.find(pat, start)
            if off == -1:
                break
            if off not in keep:
                data[off] = 0x00
            start = off + 1


def _w32le(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", data, off, value & 0xFFFFFFFF)


def _w16le(data: bytearray, off: int, value: int) -> None:
    struct.pack_into("<H", data, off, value & 0xFFFF)


def _w16be(data: bytearray, off: int, value: int) -> None:
    struct.pack_into(">H", data, off, value & 0xFFFF)


def _sum16le(data: bytes, s: int, e: int) -> int:
    acc = 0
    for i in range(s, e, 2):
        word = data[i]
        if i + 1 < len(data):
            word |= data[i + 1] << 8
        acc = (acc + word) & 0xFFFFFFFF
    return acc


def _sum16le16(data: bytes, s: int, e: int) -> int:
    acc = 0
    for i in range(s, e, 2):
        acc = (acc + (data[i] | (data[i + 1] << 8))) & 0xFFFF
    return acc


def _crc32(data: bytes, s: int, e_incl: int) -> int:
    crc = 0xFFFFFFFF
    for i in range(s, e_incl + 1):
        crc = (crc >> 8) ^ _CRC32_TABLE[(crc ^ data[i]) & 0xFF]
    return crc ^ 0xFFFFFFFF


def _crc32_cont(data: bytes, s: int, e_incl: int, prev: int) -> int:
    crc = prev ^ 0xFFFFFFFF
    for i in range(s, e_incl + 1):
        crc = (crc >> 8) ^ _CRC32_TABLE[(crc ^ data[i]) & 0xFF]
    return crc ^ 0xFFFFFFFF


_CRC32_TABLE = [
    0x00000000, 0x77073096, 0xEE0E612C, 0x990951BA, 0x076DC419, 0x706AF48F,
    0xE963A535, 0x9E6495A3, 0x0EDB8832, 0x79DCB8A4, 0xE0D5E91E, 0x97D2D988,
    0x09B64C2B, 0x7EB17CBD, 0xE7B82D07, 0x90BF1D91, 0x1DB71064, 0x6AB020F2,
    0xF3B97148, 0x84BE41DE, 0x1ADAD47D, 0x6DDDE4EB, 0xF4D4B551, 0x83D385C7,
    0x136C9856, 0x646BA8C0, 0xFD62F97A, 0x8A65C9EC, 0x14015C4F, 0x63066CD9,
    0xFA0F3D63, 0x8D080DF5, 0x3B6E20C8, 0x4C69105E, 0xD56041E4, 0xA2677172,
    0x3C03E4D1, 0x4B04D447, 0xD20D85FD, 0xA50AB56B, 0x35B5A8FA, 0x42B2986C,
    0xDBBBC9D6, 0xACBCF940, 0x32D86CE3, 0x45DF5C75, 0xDCD60DCF, 0xABD13D59,
    0x26D930AC, 0x51DE003A, 0xC8D75180, 0xBFD06116, 0x21B4F4B5, 0x56B3C423,
    0xCFBA9599, 0xB8BDA50F, 0x2802B89E, 0x5F058808, 0xC60CD9B2, 0xB10BE924,
    0x2F6F7C87, 0x58684C11, 0xC1611DAB, 0xB6662D3D, 0x76DC4190, 0x01DB7106,
    0x98D220BC, 0xEFD5102A, 0x71B18589, 0x06B6B51F, 0x9FBFE4A5, 0xE8B8D433,
    0x7807C9A2, 0x0F00F934, 0x9609A88E, 0xE10E9818, 0x7F6A0DBB, 0x086D3D2D,
    0x91646C97, 0xE6635C01, 0x6B6B51F4, 0x1C6C6162, 0x856530D8, 0xF262004E,
    0x6C0695ED, 0x1B01A57B, 0x8208F4C1, 0xF50FC457, 0x65B0D9C6, 0x12B7E950,
    0x8BBEB8EA, 0xFCB9887C, 0x62DD1DDF, 0x15DA2D49, 0x8CD37CF3, 0xFBD44C65,
    0x4DB26158, 0x3AB551CE, 0xA3BC0074, 0xD4BB30E2, 0x4ADFA541, 0x3DD895D7,
    0xA4D1C46D, 0xD3D6F4FB, 0x4369E96A, 0x346ED9FC, 0xAD678846, 0xDA60B8D0,
    0x44042D73, 0x33031DE5, 0xAA0A4C5F, 0xDD0D7CC9, 0x5005713C, 0x270241AA,
    0xBE0B1010, 0xC90C2086, 0x5768B525, 0x206F85B3, 0xB966D409, 0xCE61E49F,
    0x5EDEF90E, 0x29D9C998, 0xB0D09822, 0xC7D7A8B4, 0x59B33D17, 0x2EB40D81,
    0xB7BD5C3B, 0xC0BA6CAD, 0xEDB88320, 0x9ABFB3B6, 0x03B6E20C, 0x74B1D29A,
    0xEAD54739, 0x9DD277AF, 0x04DB2615, 0x73DC1683, 0xE3630B12, 0x94643B84,
    0x0D6D6A3E, 0x7A6A5AA8, 0xE40ECF0B, 0x9309FF9D, 0x0A00AE27, 0x7D079EB1,
    0xF00F9344, 0x8708A3D2, 0x1E01F268, 0x6906C2FE, 0xF762575D, 0x806567CB,
    0x196C3671, 0x6E6B06E7, 0xFED41B76, 0x89D32BE0, 0x10DA7A5A, 0x67DD4ACC,
    0xF9B9DF6F, 0x8EBEEFF9, 0x17B7BE43, 0x60B08ED5, 0xD6D6A3E8, 0xA1D1937E,
    0x38D8C2C4, 0x4FDFF252, 0xD1BB67F1, 0xA6BC5767, 0x3FB506DD, 0x48B2364B,
    0xD80D2BDA, 0xAF0A1B4C, 0x36034AF6, 0x41047A60, 0xDF60EFC3, 0xA867DF55,
    0x316E8EEF, 0x4669BE79, 0xCB61B38C, 0xBC66831A, 0x256FD2A0, 0x5268E236,
    0xCC0C7795, 0xBB0B4703, 0x220216B9, 0x5505262F, 0xC5BA3BBE, 0xB2BD0B28,
    0x2BB45A92, 0x5CB36A04, 0xC2D7FFA7, 0xB5D0CF31, 0x2CD99E8B, 0x5BDEAE1D,
    0x9B64C2B0, 0xEC63F226, 0x756AA39C, 0x026D930A, 0x9C0906A9, 0xEB0E363F,
    0x72076785, 0x05005713, 0x95BF4A82, 0xE2B87A14, 0x7BB12BAE, 0x0CB61B38,
    0x92D28E9B, 0xE5D5BE0D, 0x7CDCEFB7, 0x0BDBDF21, 0x86D3D2D4, 0xF1D4E242,
    0x68DDB3F8, 0x1FDA836E, 0x81BE16CD, 0xF6B9265B, 0x6FB077E1, 0x18B74777,
    0x88085AE6, 0xFF0F6A70, 0x66063BCA, 0x11010B5C, 0x8F659EFF, 0xF862AE69,
    0x616BFFD3, 0x166CCF45, 0xA00AE278, 0xD70DD2EE, 0x4E048354, 0x3903B3C2,
    0xA7672661, 0xD06016F7, 0x4969474D, 0x3E6E77DB, 0xAED16A4A, 0xD9D65ADC,
    0x40DF0B66, 0x37D83BF0, 0xA9BCAE53, 0xDEBB9EC5, 0x47B2CF7F, 0x30B5FFE9,
    0xBDBDF21C, 0xCABAC28A, 0x53B39330, 0x24B4A3A6, 0xBAD03605, 0xCDD70693,
    0x54DE5729, 0x23D967BF, 0xB3667A2E, 0xC4614AB8, 0x5D681B02, 0x2A6F2B94,
    0xB40BBE37, 0xC30C8EA1, 0x5A05DF1B, 0x2D02EF8D,
]


def _sumb_pages(data: bytes, s: int, e: int) -> int:
    total = 0
    i = s
    while i < e:
        total = (total + (data[i] | (data[i + 1] << 8)) + (data[i + 0x1FFE] | (data[i + 0x1FFF] << 8))) & 0xFFFFFFFF
        i += 0x2000
    return total


def _write_me7_tail(data: bytearray) -> None:
    """Place the descriptor at 0x600 and write the (v, ~v) main tail.

    The store cells at file_end-0x20 lie INSIDE the summed blocks, so
    solve the fixed point with one adjustable word at 0x21000:
    S + v_lo + v_hi + (0xFFFF-v_lo) + (0xFFFF-v_hi) ≡ v (mod 2^32).
    """
    n = len(data)
    struct.pack_into("<4I", data, 0x600, _ME7_BASE, _ME7_BASE + 0xFBFF,
                     _ME7_BASE + 0x20000, _ME7_BASE + (0x7FFFF if n == 0x80000 else 0xFFFFF))
    _w16le(data, 0x21000, 0)
    for off in (n - 0x20, n - 0x1E, n - 0x1C, n - 0x1A):
        _w16le(data, off, 0)
    s = (_sum16le(bytes(data), 0, 0xFC00) + _sum16le(bytes(data), 0x20000, n)) & 0xFFFFFFFF
    delta = (2 - s) & 0xFFFF
    v_hi = ((s + delta + 0x1FFFE) >> 16) & 0xFFFF
    _w16le(data, 0x21000, delta)
    val = (v_hi << 16) & 0xFFFFFFFF
    _w32le(data, n - 0x20, val)
    _w32le(data, n - 0x1C, ~val & 0xFFFFFFFF)


def _write_803c_block(data: bytearray, s1: int, e1: int, s2: int, e2: int) -> None:
    _w32le(data, 0x8030, e1 + _ME7_BASE)
    _w32le(data, 0x8034, s2 - 2 + _ME7_BASE)
    _w32le(data, 0x8014, e2 + _ME7_BASE)
    total = (_sumb_pages(bytes(data), s1, e1) + _sumb_pages(bytes(data), s2, e2)) & 0xFFFF
    _w16le(data, 0x803C, total)


def build_me7xx_type1() -> bytes:
    """512 KB type-1 ME7: 3 CRC32 zones + 803C block + main tail."""
    n = 0x80000
    data = bytearray(os.urandom(n))
    eb = bytes.fromhex("2054C4500200F0C8F0D9D4E00200")
    cs6 = bytes.fromhex("E6F4FFFFE6F5FFFF")
    cs1 = bytes.fromhex("F0545C2520545C2520545C45F085")
    rec = bytes.fromhex("00008000FFFB800000008200FFFF")

    data[0x100 : 0x100 + len(rec)] = rec
    data[0x200 : 0x200 + len(eb)] = eb
    # 3 CRC32 zones: zone 1's start is 64 KB-aligned (high-word encoded),
    # zones 2/3 carry their low 16 bits
    zones = [(0x10000, 0x11FFF), (0x1400, 0x15FF), (0x1800, 0x19FF)]
    stores = [0x1C00, 0x1E00, 0x2000]
    cs6_offs = [0x300, 0x340, 0x380]
    cs1_offs = [0x400, 0x450, 0x4A0]
    for off in cs6_offs:
        data[off : off + len(cs6)] = cs6
    for off in cs1_offs:
        data[off : off + len(cs1)] = cs1
    # store address encodings (C166 split-word reads)
    for off, store in zip(cs6_offs, stores):
        value = store + _ME7_BASE
        _w16le(data, off + 0x22, value & 0xFFFF)
        _w16le(data, off + 0x26, value >> 16)
    # zone 1: start hi word at +0x14/+0x15, end low word at +0x38
    s, e = zones[0]
    hi = (s + _ME7_BASE) >> 16
    data[cs1_offs[0] + 0x14] = hi & 0xFF
    data[cs1_offs[0] + 0x15] = hi >> 8
    _w16le(data, cs1_offs[0] + 0x38, (e + 1 + _ME7_BASE) - (hi << 16))
    # zones 2/3: start lo at +0x12, hi at +0x16/+0x17, end lo at +0x3A
    for off, (s, e) in zip(cs1_offs[1:3], zones[1:3]):
        hi = (s + _ME7_BASE) >> 16
        _w16le(data, off + 0x12, (s + _ME7_BASE) & 0xFFFF)
        data[off + 0x16] = hi & 0xFF
        data[off + 0x17] = hi >> 8
        _w16le(data, off + 0x3A, (e + 1 + _ME7_BASE) - (hi << 16))
    # CRC32 per zone (end inclusive), stores direct u32 LE
    for (s, e), store in zip(zones, stores):
        _w32le(data, store, _crc32(bytes(data), s, e))
    _scrub(data, (eb, bytes.fromhex("F0740871F0582075F2F4"),
                  bytes.fromhex("0841F0582045C4400200F0C8F0D9D4E00200"),
                  bytes.fromhex("88508840F0C8F0D9D4E00600DA")),
           (0x200,))
    _write_803c_block(data, 0, 0x2000, 0x2000, 0x4000)
    _write_me7_tail(data)
    return bytes(data)


def build_me7xx_type6() -> bytes:
    """1 MB type-6 ME7: sum8 over 4 zones (v, ~v) + 803C + main tail."""
    n = 0x100000
    data = bytearray(os.urandom(n))
    eb4 = bytes.fromhex("F0740871F0582075F2F4")
    rec = bytes.fromhex("00008000FFFB800000008200FFFF")

    data[0x100 : 0x100 + len(rec)] = rec
    sigs = [0x500, 0x800, 0xB00, 0xE00]  # ≥0x290 apart: encodings span off-0x48..off+0x247
    for off in sigs:
        data[off : off + len(eb4)] = eb4
    zones = [(0x1000, 0x11FF), (0x1400, 0x15FF), (0x1800, 0x19FF), (0x1C00, 0x1DFF)]
    store = 0x4000
    # zone encodings: split-word C166 reads before each sig
    for i, (s, e) in enumerate(zones):
        off = sigs[i]
        if i == 2:
            so, se = 0x48, 0x28
        else:
            so, se = 0x44, 0x24
        data[off - so] = (s + _ME7_BASE) & 0xFF
        data[off - so + 1] = ((s + _ME7_BASE) >> 8) & 0xFF
        data[off - so + 4] = ((s + _ME7_BASE) >> 16) & 0xFF
        data[off - so + 5] = ((s + _ME7_BASE) >> 24) & 0xFF
        data[off - se] = (e + _ME7_BASE) & 0xFF
        data[off - se + 1] = ((e + _ME7_BASE) >> 8) & 0xFF
        data[off - se + 4] = ((e + _ME7_BASE) >> 16) & 0xFF
        data[off - se + 5] = ((e + _ME7_BASE) >> 24) & 0xFF
    # store encoding: +0x242/+0x243/+0x246/+0x247 of first sig
    value = store + _ME7_BASE
    data[sigs[0] + 0x242] = value & 0xFF
    data[sigs[0] + 0x243] = (value >> 8) & 0xFF
    data[sigs[0] + 0x246] = (value >> 16) & 0xFF
    data[sigs[0] + 0x247] = (value >> 24) & 0xFF
    total = sum(sum(data[s : e + 1]) for s, e in zones) & 0xFFFFFFFF
    _w32le(data, store, total)
    _w32le(data, store + 4, ~total & 0xFFFFFFFF)
    _scrub(data, (bytes.fromhex("2054C4500200F0C8F0D9D4E00200"),
                  bytes.fromhex("0841F0582045C4400200F0C8F0D9D4E00200"),
                  bytes.fromhex("88508840F0C8F0D9D4E00600DA")),
           ())
    _write_803c_block(data, 0, 0x2000, 0x2000, 0x4000)
    _write_me7_tail(data)
    return bytes(data)


def build_me3x() -> bytes:
    """128 KB M3.x: sum8 from page marker 0x6900 to 0xBEFF, BE16 triplets."""
    n = 0x20000
    data = bytearray(os.urandom(n))
    _w16le(data, 0x6900, 0x6900)  # page marker (LE16 == address)
    calc = sum(data[0x6900 : 0xBF00]) & 0xFFFF
    _w16be(data, 0xBF04, calc)
    _w16be(data, 0xBF02, 0)
    _w16be(data, 0xBF00, calc)
    return bytes(data)


def build_m797() -> bytes:
    """512 KB Hyundai M7.9.7: 5 zone sums + main tail at 0x7FFE0."""
    n = 0x80000
    data = bytearray(os.urandom(n))
    sig = bytes.fromhex("00008000FD5E800000808000FFFB8000")
    off = 0x200
    data[off : off + len(sig)] = sig
    # entries 0-1 are fixed by the sig: [0, 0x5EFD), [0x8000, 0xFBFF)
    zones = [(0x0000, 0x5EFD), (0x8000, 0xFBFF), (0x6000, 0x6400),
             (0x6800, 0x6C00), (0x7000, 0x7400)]
    for i in range(2, 5):
        _w32le(data, off + 8 * i, zones[i][0] + _ME7_BASE)
        _w32le(data, off + 8 * i + 4, zones[i][1] + _ME7_BASE)
    total = 0
    for s, e in zones:
        total = (total + _sum16le(bytes(data), s, e)) & 0xFFFFFFFF
    _w32le(data, 0x7FFE0, total)
    _w32le(data, 0x7FFE4, ~total & 0xFFFFFFFF)
    return bytes(data)


def build_m798() -> bytes:
    """832 KB Hyundai M7.9.8: 3 fixed zones + block checksum + multipoint."""
    n = 0xD0000
    data = bytearray(os.urandom(n))
    sig = bytes.fromhex("F0EAF0FBE6FCF5FFE09D0D13DC09")
    cs = bytes.fromhex("DC0DA88CE00900E810F9F08CF01D06F8")
    data[0x100 : 0x100 + len(sig)] = sig
    off1, off2 = 0x200, 0x400
    data[off1 : off1 + len(cs)] = cs
    data[off2 : off2 + len(cs)] = cs
    # block zones (outside the fixed zones) + store encoding at 0x10000
    bz1, bz2 = (0x6000, 0x6400), (0x6800, 0x6C00)
    _w32le(data, off1 - 0x08, bz1[0])
    _w32le(data, off1 + 0x26, bz1[1])
    _w32le(data, off2 - 0x08, bz2[0])
    _w32le(data, off2 + 0x26, bz2[1])
    _w16le(data, off2 + 0x3C, 0x0000)  # store = 0x10000: w=0, x=4, y=0
    data[off2 + 0x38] = 0x04
    data[off2 + 0x39] = 0x00
    # multipoint descriptor at 0xBBBDE (inside fixed zone 2)
    mcs = 0xBBBDE
    _w32le(data, mcs, 0x2000)
    _w32le(data, mcs + 4, 0x2400)
    mval = _sum16le(bytes(data), 0x2000, 0x2400)
    _w32le(data, mcs + 8, mval)
    _w32le(data, mcs + 12, ~mval & 0xFFFFFFFF)
    # fixed zones (end exclusive), stores (v, ~v)
    for s, e, store in ((0x18000, 0x9FFF5, 0x9FFF6),
                        (0xA0000, 0xBFFF5, 0xBFFF6),
                        (0xC0000, 0xCFFF5, 0xCFFF6)):
        val = _sum16le(bytes(data), s, e)
        _w32le(data, store, val)
        _w32le(data, store + 4, ~val & 0xFFFFFFFF)
    # block checksum after fixed zones (its zones are outside them)
    total = (_sumb_pages(bytes(data), *bz1) + _sumb_pages(bytes(data), *bz2)) & 0xFFFFFFFF
    _w32le(data, 0x10000, total)
    return bytes(data)


def build_china797() -> bytes:
    """1 MB China M7.9.7: 2 zone sums, store (v, ~v) at 0xFFFE8.

    All four store words lie INSIDE the summed range, so the builder
    solves the fixed point: with v_lo = 0 and v_hi free, total ≡ v
    requires Z + delta + 0x1FFFE ≡ v_hi<<16 (Z = sum with the store
    words and the adjustable word zeroed).
    """
    n = 0x100000
    data = bytearray(os.urandom(n))
    sig = bytes.fromhex("00008000FFFF800000008100FFFF8F00")
    data[0x100 : 0x100 + len(sig)] = sig
    for off in (0x12000, 0xFFFE8, 0xFFFEA, 0xFFFEC, 0xFFFEE):
        _w16le(data, off, 0)
    z = (_sum16le(bytes(data), 0x0000, 0xFFFF)
         + _sum16le(bytes(data), 0x10000, 0xFFFFF)) & 0xFFFFFFFF
    delta = (2 - z) & 0xFFFF
    v_hi = ((z + delta + 0x1FFFE) >> 16) & 0xFFFF
    _w16le(data, 0x12000, delta)
    val = (v_hi << 16) & 0xFFFFFFFF
    _w32le(data, 0xFFFE8, val)
    _w32le(data, 0xFFFEC, ~val & 0xFFFFFFFF)
    return bytes(data)


def build_me745() -> bytes:
    """832 KB Citroen ME7.4.5: block pages + multipoint + 3 CRC32."""
    n = 0xD0000
    data = bytearray(os.urandom(n))
    sig = bytes.fromhex("88E088D0E6FC0020E09DE6FEFFFFE0AF")
    cs1 = bytes.fromhex("00000000FF3F0000")
    cs2 = bytes.fromhex("00C00C00D3FF0C00")
    data[0x100 : 0x100 + len(sig)] = sig
    data[0xC1000 : 0xC1000 + len(cs1)] = cs1
    data[0xC1200 : 0xC1200 + len(cs2)] = cs2
    # multipoint descriptors between cs1 and cs2 (inside CRC32 tail zone)
    for i, mcs in enumerate((0xC1020, 0xC1040)):
        s, e = 0x2000 + 0x400 * i, 0x2400 + 0x400 * i
        _w32le(data, mcs, s)
        _w32le(data, mcs + 4, e)
        val = _sum16le(bytes(data), s, e)
        _w32le(data, mcs + 8, val)
        _w32le(data, mcs + 12, ~val & 0xFFFFFFFF)
    # block pages (zones include multipoint summed regions)
    bz = [(0x02000, 0x07FFF), (0x20000, 0x8FFFF), (0x92000, 0xAFFFF)]
    total = 0
    for s, e in bz:
        total = (total + _sumb_pages(bytes(data), s, e)) & 0xFFFFFFFF
    _w32le(data, 0xB7FFA, total)
    # CRC32 #1 (three chained zones)
    z = [(0x00000, 0x07FFF), (0x20000, 0x8FFFF), (0x92000, 0xAFFFF)]
    crc = _crc32(bytes(data), *z[0])
    crc = _crc32_cont(bytes(data), *z[1], crc)
    crc = _crc32_cont(bytes(data), *z[2], crc)
    _w32le(data, 0xCFFD8, crc)
    # CRC32 #2 and #3
    _w32le(data, 0x1FFFC, _crc32(bytes(data), 0x18000, 0x1F9CF))
    _w32le(data, 0xCFFD4, _crc32(bytes(data), 0xB0000, 0xCFFD3))
    return bytes(data)


def build_samand() -> bytes:
    """832 KB Sagem: 2× sum8 stored LE16."""
    n = 0xD0000
    data = bytearray(os.urandom(n))
    sig = bytes.fromhex("DC06A84108121860C0850035C0950035")
    data[0x100 : 0x100 + len(sig)] = sig
    calc1 = (sum(data[0x02000 : 0x04000]) + sum(data[0x10000 : 0x6FFFE])) & 0xFFFF
    calc2 = sum(data[0x72806 : 0x80000]) & 0xFFFF
    _w16le(data, 0x6FFFE, calc1)
    _w16le(data, 0x72804, calc2)
    return bytes(data)


BUILDERS = {
    # (builder, detector, subtype, covered-byte offset for corruption test)
    "vag_me7xx": (build_me7xx_type1, detect_me7xx, 1, 0x1100),
    "me3x": (build_me3x, detect_me3x, 0, 0x7000),
    "m797": (build_m797, detect_m797, 0, 0x1100),
    "m798": (build_m798, detect_m798, 0, 0x18000),
    "china797": (build_china797, detect_china797, 0, 0x1100),
    "me745": (build_me745, detect_me745, 0, 0x1100),
    "samand": (build_samand, detect_samand, 0, 0x2000),
}


# ---------------------------------------------------------------------------
# Synthetic validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(BUILDERS))
def test_synthetic_all_checks_ok(family):
    builder, detector, _, _ = BUILDERS[family]
    data = builder()
    profile = detector(data)
    assert profile is not None, f"{family} not recognized"
    assert profile.subtype == BUILDERS[family][2]
    statuses = {c.name: c.status for c in profile.checks}
    assert statuses, f"{family} has no checks"
    assert all(s == "ok" for s in statuses.values()), f"{family}: {statuses}"


@pytest.mark.parametrize("family", sorted(BUILDERS))
def test_synthetic_corruption_detected(family):
    builder, detector, _, corrupt_off = BUILDERS[family]
    data = bytearray(builder())
    data[corrupt_off] ^= 0xFF
    profile = detector(bytes(data))
    assert profile is not None
    statuses = {c.name: c.status for c in profile.checks}
    assert any(s == "stale" for s in statuses.values()), f"{family}: {statuses}"


@pytest.mark.parametrize("family", sorted(BUILDERS))
def test_synthetic_wrong_size_rejected(family):
    builder, detector, _, _ = BUILDERS[family]
    data = builder()
    assert detector(data[: len(data) - 1]) is None


def test_me7xx_type1_and_type6_subtypes():
    t1 = detect_me7xx(build_me7xx_type1())
    t6 = detect_me7xx(build_me7xx_type6())
    assert t1 is not None and t1.subtype == 1
    assert t6 is not None and t6.subtype == 6
    names1 = {c.name for c in t1.checks}
    names6 = {c.name for c in t6.checks}
    assert "crc32_zone1" in names1 and "sum8_zones" in names6


def test_registry_covers_all_families():
    from openremap.core.services.checksums.ironfelix import _DETECTORS

    assert set(IRONFELIX_FAMILIES) == set(_DETECTORS)
    assert len(IRONFELIX_FAMILIES) == 9


def test_detect_all_returns_me7xx_for_synthetic():
    profs = detect_all(build_me7xx_type1())
    assert [p.family for p in profs] == ["vag_me7xx"]


def test_m798_synthetic_multipoint_verifies():
    profile = detect_m798(build_m798())
    assert profile is not None
    assert profile.multipoint_valid >= 1
    assert profile.multipoint_unverified == 0


def test_me745_synthetic_multipoint_verifies():
    profile = detect_me745(build_me745())
    assert profile is not None
    assert profile.multipoint_valid >= 1


# ---------------------------------------------------------------------------
# Real-corpus validation (skip-guarded — tests/data is gitignored)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_me71_family_recognition():
    p = DATA / "ECUs" / "Bosch" / "ME7.1"
    files = [f for f in p.iterdir() if f.is_file() and f.stat().st_size >= 0x80000]
    assert len(files) >= 50
    recognized = allok = 0
    for f in files:
        data = f.read_bytes()
        profile = detect_me7xx(data)
        assert profile is not None, f"{f.name}: ME7.1 file not recognized"
        recognized += 1
        statuses = [c.status for c in profile.checks]
        if all(s == "ok" for s in statuses):
            allok += 1
    assert recognized == len(files)
    # reference corpus contains modified files; require a solid majority
    assert allok >= recognized * 0.7, f"only {allok}/{recognized} all-ok"


@pytest.mark.skipif(not HAS_ME71, reason="tests/data/ECUs/Bosch/ME7.1 corpus missing")
def test_corpus_me71_main_tail_verifies():
    p = DATA / "ECUs" / "Bosch" / "ME7.1"
    files = [f for f in p.iterdir() if f.is_file() and f.stat().st_size >= 0x80000]
    ok = 0
    for f in files:
        profile = detect_me7xx(f.read_bytes())
        assert profile is not None
        for c in profile.checks:
            if c.name == "main_tail":
                if c.status == "ok":
                    ok += 1
    assert ok >= len(files) * 0.8


@pytest.mark.skipif(not HAS_M38, reason="tests/data/ECUs/Bosch/M3.8 corpus missing")
def test_corpus_me3x_family_recognition():
    p = DATA / "ECUs" / "Bosch" / "M3.8"
    files = [f for f in p.iterdir() if f.is_file() and f.stat().st_size in (0x20000, 0x40000)]
    assert len(files) >= 10
    recognized = allok = 0
    for f in files:
        profile = detect_me3x(f.read_bytes())
        if profile is None:
            continue  # a few modified files lack the marker table
        recognized += 1
        if all(c.status == "ok" for c in profile.checks):
            allok += 1
    assert recognized >= len(files) * 0.8, f"only {recognized}/{len(files)} recognized"
    assert allok >= recognized * 0.8, f"only {allok}/{recognized} all-ok"


# ---------------------------------------------------------------------------
# Siemens GS20 / SMG II (MS4X community tool — CRC-16/ARC over fixed ranges)
# ---------------------------------------------------------------------------

HAS_GS20 = (DATA / "ECUs" / "Siemens" / "GS20").is_dir()

GS20_PAIRS = [
    ("GS20_90C0_64KB.bin", "ok"),
    ("GS20_90C0_64KB_mod_showgear.bin", "stale"),
    ("GS20_90C0_64KB_mod_showgear_corrected.bin", "stale"),
    ("GS20_90C0_64KB_mod_tcc.bin", "stale"),
]


@pytest.mark.skipif(not HAS_GS20, reason="tests/data/ECUs/Siemens/GS20 corpus missing")
@pytest.mark.parametrize("filename,expected", GS20_PAIRS)
def test_corpus_gs20_golden_pair(filename, expected):
    """Factory base verifies; the modified files are stale.  Notably the
    'corrected' file is stale too — its author wrote the CRC byte-swapped
    (0x190B as 19 0B) instead of LE (0B 19)."""
    data = (DATA / "ECUs" / "Siemens" / "GS20" / filename).read_bytes()
    profile = detect_gs20(data)
    assert profile is not None
    c = profile.checks[0]
    assert c.status == expected, f"{filename}: {c.status} != {expected}"


def build_gs20_code() -> bytes:
    """256 KB GS20 program variant: regions [0,511]+[640,261631], store @261836."""
    n = 0x40000
    data = bytearray(os.urandom(n))
    v = 0
    for i in range(0, 512):
        v = (v + data[i]) & 0xFFFF  # placeholder to keep the builder honest
    total = _crc16_arc(bytes(data), ((0, 511), (640, 261631)))
    _w16le(data, 261836, total)
    return bytes(data)


def build_smg2() -> bytes:
    """32 KB SMG2 variant: init 0x7878, region [8416,30911], store @8320."""
    n = 0x8000
    data = bytearray(os.urandom(n))
    total = _crc16_arc(bytes(data), ((8416, 30911),), 0x7878)
    _w16le(data, 8320, total)
    return bytes(data)


def _crc16_arc(data: bytes, ranges, init: int = 0) -> int:
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


def test_synthetic_gs20_code_ok():
    profile = detect_gs20(build_gs20_code())
    assert profile is not None
    assert all(c.status == "ok" for c in profile.checks)


def test_synthetic_gs20_code_stale():
    data = bytearray(build_gs20_code())
    data[0x10000] ^= 0xFF
    profile = detect_gs20(bytes(data))
    assert profile is not None
    assert profile.checks[0].status == "stale"


def test_synthetic_smg2_ok():
    profile = detect_smg2(build_smg2())
    assert profile is not None
    assert all(c.status == "ok" for c in profile.checks)


def test_synthetic_smg2_stale():
    data = bytearray(build_smg2())
    data[9000] ^= 0xFF
    profile = detect_smg2(bytes(data))
    assert profile is not None
    assert profile.checks[0].status == "stale"


def test_gs20_wrong_size_rejected():
    data = build_gs20_code()
    assert detect_gs20(data[: len(data) - 1]) is None
    data = build_smg2()
    assert detect_smg2(data[: len(data) - 1]) is None
