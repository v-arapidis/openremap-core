"""
Siemens MS43 (BMW M54 E46 ECU) checksum detection.

Cracked 2026-08-15 by disassembling the MS43 boot code (C167) with the
C166 decoder from nefmoto.py, then validating against the factory base
binary:

- Algorithm: CRC-16/ARC (poly 0x8005 reflected), byte-wise, over
  multi-block ranges defined by a descriptor table next to each slot:
  [checksum u16][num blocks u16][per block: start u32 LE, end u32 LE
  (both inclusive)].
- Slots: boot @0x3C24, program @0x6FDE0, calibration @0x73FE0.
- Coordinates: boot and calibration blocks are file offsets; program
  blocks are memory addresses (file offset = address - 0x80000).
- Init values are NOT fixed — each section's init is a 16-bit word read
  from the firmware's ID string areas (big-endian ASCII pairs, matching
  the boot code's runtime reads):
    boot:     BE16 @ 0x3FE6  (UIF area, "--" -> 0x2D2D)
    program:  BE16 @ 0x3C34  (SW-ID area, "00" -> 0x3030)
    cal:      BE16 @ 0x6FFBF (SW-number tail, "96" -> 0x3936)
- The two 32-bit "monitor" addition checksums (@0x6FDAE program,
  @0x72FFC calibration) are RUNTIME checks: the program code loads the
  monitored-value pointers from the C167's on-chip XRAM (mem 0xE072 /
  0xE06E) and sums the runtime _mon values (routines near in-file
  0x50E22 / 0x511B6), comparing against the flash-stored factory values.
  They cannot be verified from a static dump without emulating the
  firmware startup (the runtime values include adaptations); the
  community workaround is to disable the check (lc_swi_cal_mon_cks =
  165 / 0xA5).  The slots are reported as "unverified" — honest, not
  silent.

Validation: factory base verifies 3/3 OK; the tuned files in the corpus
(calibration edits without checksum recalculation) correctly verify
boot+program OK and calibration STALE.

Attribution: MS4X wiki (https://www.ms4x.net) — slot locations; the
algorithm itself from boot-code disassembly of the factory binary.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openremap._rust import crc16_arc as _rust_crc16_arc  # type: ignore[import-untyped]

# slot layout constants
_BOOT_SLOT = 0x3C24
_PROG_SLOT = 0x6FDE0
_CAL_SLOT = 0x73FE0
_PROG_MON = 0x6FDAE
_CAL_MON = 0x72FFC

_PROG_BASE = 0x80000  # program blocks are memory addresses

_MAX_BLOCKS = 16


def crc16_arc(data: bytes, blocks: list[tuple[int, int]], init: int) -> int:
    """CRC-16/ARC (poly 0x8005 reflected) over inclusive byte blocks.

    Runs natively in Rust (`_rs/src/crc16.rs`) — ~80x faster than the
    previous Python byte loop; parity verified against the standard
    check value ("123456789" → 0xBB3D), the MS43 corpus, and synthetic
    edges (2026-08-15, see docs/rust-migration-audit.md)."""
    return _rust_crc16_arc(data, [(s, e) for s, e in blocks], init)


@dataclass(frozen=True)
class Ms43CrcCheck:
    name: str  # "boot" | "program" | "calibration"
    slot: int
    status: str  # "ok" | "stale" | "absent"
    stored: int | None
    expected: int | None
    init_offset: int | None
    blocks: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Ms43MonSlot:
    name: str
    slot: int
    stored: int | None
    status: str  # "unverified" — runtime check, not statically verifiable


@dataclass(frozen=True)
class Ms43Profile:
    crcs: tuple[Ms43CrcCheck, ...]
    mons: tuple[Ms43MonSlot, ...]

    @property
    def ok(self) -> int:
        return sum(1 for c in self.crcs if c.status == "ok")

    @property
    def total(self) -> int:
        return len(self.crcs)


def _u16(data: bytes, off: int) -> int | None:
    if off < 0 or off + 2 > len(data):
        return None
    return data[off] | (data[off + 1] << 8)


def _u16be(data: bytes, off: int) -> int | None:
    if off < 0 or off + 2 > len(data):
        return None
    return (data[off] << 8) | data[off + 1]


def _read_blocks(data: bytes, slot: int, mem_base: int) -> tuple[int, tuple[tuple[int, int], ...]] | None:
    """Descriptor table after the slot: [num blocks][blocks].  Returns
    (count, blocks) or None when the table is absent/garbage."""
    count = _u16(data, slot + 2)
    if count is None or count == 0 or count > _MAX_BLOCKS:
        return None
    blocks = []
    for i in range(count):
        off = slot + 4 + i * 8
        s_lo = _u16(data, off)
        s_hi = _u16(data, off + 2)
        e_lo = _u16(data, off + 4)
        e_hi = _u16(data, off + 6)
        if None in (s_lo, s_hi, e_lo, e_hi):
            return None
        assert s_lo is not None and s_hi is not None
        assert e_lo is not None and e_hi is not None
        start = s_lo | (s_hi << 16)
        end = e_lo | (e_hi << 16)
        if mem_base:
            start -= mem_base
            end -= mem_base
        if start < 0 or end < start or end >= len(data):
            return None
        blocks.append((start, end))
    return count, tuple(blocks)


def detect_ms43(data: bytes) -> Ms43Profile | None:
    """Detect/verify the MS43 CRC16 checksums.  Returns None when the
    descriptor tables are absent (not an MS43-style file)."""
    n = len(data)
    if n < 0x80000:
        return None

    checks: list[Ms43CrcCheck] = []
    boot_blocks = _read_blocks(data, _BOOT_SLOT, 0)
    if boot_blocks is None:
        return None
    checks.append(
        _check(data, "boot", _BOOT_SLOT, boot_blocks[1], 0x3FE6, 0)
    )
    checks.append(
        _check(data, "program", _PROG_SLOT, None, 0x3C34, _PROG_BASE)
    )
    checks.append(
        _check(data, "calibration", _CAL_SLOT, None, 0x6FFBF, 0)
    )

    mons = (
        Ms43MonSlot("program", _PROG_MON, _u32le(data, _PROG_MON), "unverified"),
        Ms43MonSlot("calibration", _CAL_MON, _u32le(data, _CAL_MON), "unverified"),
    )
    return Ms43Profile(crcs=tuple(checks), mons=mons)


def _check(
    data: bytes, name: str, slot: int,
    pre_parsed: tuple[tuple[int, int], ...] | None,
    init_offset: int, mem_base: int,
) -> Ms43CrcCheck:
    stored = _u16(data, slot)
    if stored is None:
        return Ms43CrcCheck(name, slot, "absent", None, None, init_offset, ())
    if pre_parsed is not None:
        blocks = pre_parsed
    else:
        parsed = _read_blocks(data, slot, mem_base)
        if parsed is None:
            return Ms43CrcCheck(name, slot, "absent", stored, None, init_offset, ())
        blocks = parsed[1]
    init = _u16be(data, init_offset)
    if init is None:
        return Ms43CrcCheck(name, slot, "absent", stored, None, init_offset, blocks)
    expected = crc16_arc(data, list(blocks), init)
    return Ms43CrcCheck(
        name, slot,
        "ok" if expected == stored else "stale",
        stored, expected, init_offset, blocks,
    )


def _u32le(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, off)[0]
