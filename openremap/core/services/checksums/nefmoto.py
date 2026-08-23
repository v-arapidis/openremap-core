"""
NefMoto ME7 rolling + multirange checksum detection — ported from the
open-source NefMotoOpenSource project (Nefarious Motorsports ME7 ECU
Flasher, GPL-3.0, Checksum/ChecksumDetection.cs).

Unlike the main ME7 checksum (sum of u16 words over descriptor blocks,
see checksum.py), some ME7 firmware uses a *rolling* checksum: a
seed-table-driven 32-bit hash (init 0xFFFFFFFF) over one or more byte
ranges, stored inverted (~v), plus optionally a *multirange* byte-sum
(v, ~v) over the same ranges.  Both schemes are detected from the
firmware itself — NefMoto locates the checksum routines by pattern
matching C166 machine code and parses the instructions (MOV/CMPB/ADD/
ADDC/...) to extract the seed-table address, the address ranges, and
the checksum store locations.  This port is a faithful transcription of
that logic (including the pattern resources and the opcode tables).

Addresses parsed from the firmware are 24-bit flash addresses
(segment << 16 | offset); file offsets are ``addr - 0x800000`` (the
standard ME7 flash base, validated against the corpus: the pattern
resource "…0x879C0C" lands at file offset 0x79C0C in M-box files).

Attribution: NefMotoOpenSource — https://github.com/NefMoto/NefMotoOpenSource
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openremap._rust import (  # type: ignore[import-untyped]
    locate_pattern as _rust_locate_pattern,
    rolling_checksum as _rust_rolling_checksum,
)

# ---------------------------------------------------------------------------
# Pattern resources (from NefMotoOpenSource/Checksum/Resources/*.bin)
# ---------------------------------------------------------------------------

_PAT_ROLLING_SEEDS = bytes.fromhex(
    "8890888088708860" "2802e00f" "f2f6bef9f2f7c0f9" "f08cf09d0d1edc09"
    "a988f05651" "a8b9a0" "e6f46ae7" "e6f58100"
)
_PAT_ROLLING_SEEDS_MASK = bytes.fromhex(
    "ffffffffffffffffffffffffffff" "0000" "ffff0000" "ffffffffffffffffffffffff"
    "ffffffffffff" "0000" "ffff0000"
)

_PAT_ROLLING_VALUE_INIT = bytes.fromhex(
    "f3f850f93d18" "f68f3af9" "f68f3cf9" "e6fce983" "e6fd8100" "e0ae" "da89e400"
)
_PAT_ROLLING_VALUE_INIT_MASK = bytes.fromhex(
    "000000000000" "ffff" "0000ffff" "0000ffff" "0000ffff" "0000" "ff00" "ff000000"
)

_PAT_RANGES_M = bytes.fromhex(
    "e6f85242e6f98100f2f4c2f9248fc2f9" "f0545c2520545c2520545c45f0a5e0" "0b00"
    "8a10" "9b" "e6f44e7f" "e6f58100"
)
_PAT_RANGES_M_MASK = bytes.fromhex(
    "ffff0000ffff0000ffff0000ffff0000" "ffffffffffffffffffffffffffffffff"
    "ffffffffffff" "0000" "ffff0000"
)

_PAT_RANGES_C = bytes.fromhex(
    "f2f4baf9248fbaf9" "f0545c2520545c2520545c45f085" "e009"
)
_PAT_RANGES_C_MASK = bytes.fromhex(
    "ffff0000ffff0000" "ffffffffffffffffffffffffffff" "ffff"
)

_PAT_MR_FUNC = bytes.fromhex(
    "f3f8f7f947f855003d3b" "e6f89281e6f98100" "f2f4f0f9248ff0f9"
    "f0545c4520545c45f0a5e0" "0b008a109b" "e6f4dcfbe6f58100" "26f4f000"
    "385020483059" "fd03" "e6f7f0000d0c"
)
_PAT_MR_FUNC_MASK = bytes.fromhex(
    "ffff0000ffff0000ff00ffff0000ffff0000ffff0000ffff0000ffffffffffffffffffffffffffffffffffff0000ffff0000ffffffffffffffffffffff00ffffffffff00"
)

_PAT_MR_VALUE = bytes.fromhex(
    "ea3022bd0981" "f7f8f8f9e10c" "e6f4e6bee6f58a00" "da00627d" "e6000402cc0022fa"
    "ecf932fb" "eef9" "2d24"
)
_PAT_MR_VALUE_MASK = bytes.fromhex(
    "ffff0000" "ffffffff" "0000ffffffff" "0000ffff0000" "ffff0000" "ffffffffffffffff"
    "0000ffff" "0000" "ff00"
)

# inline "checksum value address" pattern (ChecksumDetection.cs line 409)
_PAT_CKSM_VALUE = bytes.fromhex("E6F472A8E6F58700DA00D87E")
_PAT_CKSM_VALUE_MASK = bytes.fromhex("FFFF0000FFFF0000FFFF0000")

_ME7_FLASH_BASE = 0x800000

# ---------------------------------------------------------------------------
# C166 instruction parsing (subset — only the instructions the detection
# uses, transcribed from ParseInstruction + the Parse* helpers)
# ---------------------------------------------------------------------------

_OPCODES: dict[int, tuple[str, int]] = {
    0xDA: ("CALLS", 4),
    0xEA: ("JMPA", 4),
    0xDC: ("EXTP", 2),
    0xD7: ("EXTP", 4),
    0x20: ("SUB", 2), 0x28: ("SUB", 2), 0x26: ("SUB", 4), 0x22: ("SUB", 4), 0x24: ("SUB", 4),
    0x00: ("ADD", 2), 0x08: ("ADD", 2), 0x06: ("ADD", 4), 0x02: ("ADD", 4), 0x04: ("ADD", 4),
    0x10: ("ADDC", 2), 0x18: ("ADDC", 2), 0x16: ("ADDC", 4), 0x12: ("ADDC", 4), 0x14: ("ADDC", 4),
    0x40: ("CMP", 2), 0x48: ("CMP", 2), 0x46: ("CMP", 4), 0x42: ("CMP", 4),
    0x41: ("CMPB", 2), 0x49: ("CMPB", 2), 0x47: ("CMPB", 4), 0x43: ("CMPB", 4),
    0x84: ("MOV", 4), 0x88: ("MOV", 2), 0x94: ("MOV", 4), 0x98: ("MOV", 2),
    0xA8: ("MOV", 2), 0xB8: ("MOV", 2), 0xC4: ("MOV", 4), 0xC8: ("MOV", 2),
    0xD4: ("MOV", 4), 0xD8: ("MOV", 2), 0xE0: ("MOV", 2), 0xE6: ("MOV", 4),
    0xE8: ("MOV", 2), 0xF0: ("MOV", 2), 0xF2: ("MOV", 4), 0xF6: ("MOV", 4),
    0xF1: ("MOVB", 2), 0xE1: ("MOVB", 2), 0xE7: ("MOVB", 4), 0xA9: ("MOVB", 2),
    0x99: ("MOVB", 2), 0xB9: ("MOVB", 2), 0x89: ("MOVB", 2), 0xC9: ("MOVB", 2),
    0xD9: ("MOVB", 2), 0xE9: ("MOVB", 2), 0xF4: ("MOVB", 4), 0xE4: ("MOVB", 4),
    0xA4: ("MOVB", 4), 0xB4: ("MOVB", 4), 0xF3: ("MOVB", 4), 0xF7: ("MOVB", 4),
}


def _parse_instruction(data: bytes, offset: int) -> tuple[str | None, int]:
    if offset >= len(data):
        return (None, 2)
    b0 = data[offset]
    if (b0 & 0x0F) == 0x0D:
        return ("JMPR", 2)
    hit = _OPCODES.get(b0)
    if hit is not None:
        return hit
    return (None, 2)


def _u16le(data: bytes, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def _data3_subswitch(data: bytes, off: int) -> tuple[int, int] | None:
    """The (data[off+1] & 0x0C) sub-switch shared by the 2-byte
    reg/immediate arithmetic forms (CMP/CMPB/SUB/ADD/ADDC 0x?8 opcodes)."""
    sub = data[off + 1] & 0x0C
    if sub in (0x08, 0x0C):
        return (0, 2)  # register operand
    return (data[off + 1] & 0x7, 2)  # #data3


def _parse_mov(data: bytes, off: int) -> tuple[int, int] | None:
    """Returns (operand2, size); size >= 2 on success (register forms
    yield 0), None when the opcode is not a MOV the detection handles."""
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x84:  # MOV [Rwn], mem
        return (_u16le(data, off + 2), 4)
    if b0 in (0x88, 0x98, 0xA8, 0xB8, 0xC8, 0xD8, 0xE8, 0xF0):
        return (0, 2)
    if b0 == 0x94:  # MOV mem, [Rwn]
        return (_u16le(data, off + 2), 4)
    if b0 == 0xC4:  # MOV [Rw+#data16], Rw
        return (_u16le(data, off + 2), 4)
    if b0 == 0xD4:  # MOV Rwn, [Rw+#data16]
        return (_u16le(data, off + 2), 4)
    if b0 == 0xE0:  # MOV Rwn, #data4
        return ((data[off + 1] & 0xF0) >> 4, 2)
    if b0 == 0xE6:  # MOV reg, #data16
        return (_u16le(data, off + 2), 4)
    if b0 in (0xF2, 0xF6):  # MOV reg, mem / MOV mem, reg
        return (_u16le(data, off + 2), 4)
    return None


def _parse_movb(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 in (0xF1, 0xA9, 0x99, 0xB9, 0x89, 0xC9, 0xD9, 0xE9):
        return (0, 2)
    if b0 == 0xE1:  # MOVB Rbn, #data4
        return (data[off + 1] >> 4, 2)
    if b0 in (0xE7, 0xF4, 0xA4, 0xF3):  # MOVB reg,#data16 / [Rw+off] / [Rwn],mem / reg,mem
        return (_u16le(data, off + 2), 4)
    if b0 in (0xE4, 0xB4, 0xF7):  # MOVB [Rw+off],Rbn / mem,[Rwn] / mem,reg
        return (_u16le(data, off + 2), 4)
    return None


def _parse_cmpb(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x41:  # CMPB Rbn, Rbm
        return (0, 2)
    if b0 == 0x49:  # CMPB Rbn, [Rwi]/[Rwi+]/#data3
        return _data3_subswitch(data, off)
    if b0 == 0x47:  # CMPB reg, #data16
        return (_u16le(data, off + 2), 4)
    return None


def _parse_cmp(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x40:  # CMP Rwn, Rwm
        return (0, 2)
    if b0 == 0x48:  # CMP Rwn, [Rwi]/[Rwi+]/#data3
        return _data3_subswitch(data, off)
    if b0 in (0x46, 0x42):  # CMP reg, #data16 / CMP reg, mem
        return (_u16le(data, off + 2), 4)
    return None


def _parse_sub(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x20:  # SUB Rwn, Rwm
        return (0, 2)
    if b0 == 0x28:  # SUB Rwn, [Rwi]/[Rwi+]/#data3
        return _data3_subswitch(data, off)
    if b0 in (0x26, 0x22):  # SUB reg, #data16 / SUB reg, mem
        return (_u16le(data, off + 2), 4)
    if b0 == 0x24:  # SUB mem, reg
        return (_u16le(data, off + 2), 4)
    return None


def _parse_add(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x00:  # ADD Rwn, Rwm
        return (0, 2)
    if b0 == 0x08:  # ADD Rwn, [Rwi]/[Rwi+]/#data3
        return _data3_subswitch(data, off)
    if b0 in (0x06, 0x02):  # ADD reg, #data16 / ADD reg, mem
        return (_u16le(data, off + 2), 4)
    if b0 == 0x04:  # ADD mem, reg
        return (_u16le(data, off + 2), 4)
    return None


def _parse_addc(data: bytes, off: int) -> tuple[int, int] | None:
    if off >= len(data):
        return None
    b0 = data[off]
    if b0 == 0x10:  # ADDC Rwn, Rwm
        return (0, 2)
    if b0 == 0x18:  # ADDC Rwn, [Rwi]/[Rwi+]/#data3
        return _data3_subswitch(data, off)
    if b0 in (0x16, 0x12):  # ADDC reg, #data16 / ADDC reg, mem
        return (_u16le(data, off + 2), 4)
    if b0 == 0x14:  # ADDC mem, reg
        return (_u16le(data, off + 2), 4)
    return None


def _locate_pattern(
    data: bytes, pat: bytes, mask: bytes,
    offset: int = 0, max_offset: int | None = None, step: int = 2,
) -> int:
    """Masked byte-pattern scan (step 2, early exit) — runs natively in
    Rust (`_rs/src/nefmoto_scan.rs`), ~370-520x faster than the previous
    Python loop; parity verified on 236 ME7.x corpus files + synthetic
    edges (2026-08-15, see docs/rust-migration-audit.md)."""
    return _rust_locate_pattern(
        data, pat, mask, offset,
        -1 if max_offset is None else max_offset,
        step,
    )


def _find_next_instruction(data: bytes, offset: int, want: str) -> tuple[int, int]:
    size = 2
    x = offset
    while x < len(data):
        ins, size = _parse_instruction(data, x)
        if ins == want:
            return (x, size)
        x += size
    return (-1, 2)


def _find_prev_instruction_sequence_start(
    data: bytes, start_offset: int, end_offset: int, sequence: list[str],
) -> tuple[int, list[int]]:
    """Scans backward from start_offset-2 for the REVERSED sequence;
    returns (sequence_start_address, instruction_sizes)."""
    if end_offset < 0:
        end_offset = 0
    x = start_offset - 2
    while x >= end_offset:
        cur = x
        sizes: list[int] = []
        found = True
        for ins in reversed(sequence):
            cur -= 2
            name, size = _parse_instruction(data, cur)
            if name == ins and size == 2:
                pass
            else:
                name2, size2 = _parse_instruction(data, cur - 2)
                if name2 == ins and size2 == 4:
                    cur -= 2
                    size = 4
                else:
                    found = False
                    break
            sizes.insert(0, size)
        if found:
            return (cur, sizes)
        x -= 2
    return (-1, [])


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollingRange:
    start: int  # file offset (memory address minus flash base)
    end: int  # file offset, INCLUSIVE


@dataclass(frozen=True)
class RollingChecksumEntry:
    """One rolling-checksum slot: inverted 32-bit value at store_offset,
    computed over the given byte ranges (chained with the init range)."""

    store_offset: int
    ranges: tuple[RollingRange, ...]
    init_range: RollingRange | None
    stored: int
    expected: int
    status: str  # "ok" | "stale"


@dataclass(frozen=True)
class MultiRangeChecksumInfo:
    """The optional multirange byte-sum: stored (v, ~v) at store_offset."""

    store_offset: int
    ranges: tuple[RollingRange, ...]
    stored: int
    inv_stored: int
    expected: int
    status: str  # "ok" | "stale" | "pair_mismatch"


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


def rolling_checksum(
    data: bytes, seed_table_offset: int, ranges: list[RollingRange],
    init: int = 0xFFFFFFFF,
) -> int:
    """NefMoto CalculateRollingChecksumForRange over the listed ranges:
    for every byte, checksum >>= 8; checksum ^= seed_table[(byte ^
    (checksum & 0xFF)) << 2].  Runs natively in Rust
    (`_rs/src/nefmoto_scan.rs`) — ~200x faster than the previous Python
    byte loop; parity verified on 236 ME7.x corpus files + synthetic
    edges (2026-08-15, see docs/rust-migration-audit.md)."""
    return _rust_rolling_checksum(
        data, seed_table_offset,
        [(r.start, r.end) for r in ranges],
        init,
    )


def multirange_checksum(data: bytes, ranges: list[RollingRange]) -> int:
    """NefMoto MultiRangeChecksum: u32 sum of bytes over the ranges."""
    total = 0
    for r in ranges:
        total += sum(data[r.start : r.end + 1])
    return total & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Detection (port of DetectRollingAndMultiRangeChecksums)
# ---------------------------------------------------------------------------


def _mem_to_file(addr: int, base: int = _ME7_FLASH_BASE) -> int:
    return addr - base


def detect_me7_rolling(
    data: bytes, base: int = _ME7_FLASH_BASE,
) -> list[RollingChecksumEntry] | None:
    """Port of NefMoto DetectRollingAndMultiRangeChecksums (rolling part).

    Returns one entry per checksum slot (in firmware order), or None when
    the firmware has no rolling-checksum structure.
    """
    n = len(data)
    seeds_off = _locate_pattern(data, _PAT_ROLLING_SEEDS, _PAT_ROLLING_SEEDS_MASK, 0, n, 2)
    if seeds_off < 0:
        return None

    current = seeds_off + len(_PAT_ROLLING_SEEDS) - 8
    parsed = _parse_mov(data, current)
    if parsed is None:
        return None
    address = parsed[0]
    current += parsed[1]
    parsed = _parse_mov(data, current)
    if parsed is None:
        return None
    segment = parsed[0]
    seed_table_addr = (segment << 16) | address
    seed_table_offset = _mem_to_file(seed_table_addr, base)

    saved_current = current + parsed[1]

    # initialization range (dual-checksum firmware only)
    init_range: RollingRange | None = None
    is_using_dual = False
    init_hit = _locate_pattern(
        data, _PAT_ROLLING_VALUE_INIT, _PAT_ROLLING_VALUE_INIT_MASK,
        saved_current, saved_current + 4096, 2,
    )
    if init_hit > 0:
        # The reference gates dual mode on the pattern landing on a MOV
        # opcode — fragile across firmware variants (the 6 leading mask
        # bytes are wildcards).  Corpus evidence: with dual mode on, every
        # such file verifies OK; a wrong guess can only yield "stale",
        # never a false "ok".  So: pattern found => dual mode.
        is_using_dual = True
        init_hit += 14
        parsed = _parse_mov(data, init_hit)
        if parsed is None:
            return None
        init_address = parsed[0]
        init_hit += parsed[1]
        parsed = _parse_mov(data, init_hit)
        if parsed is None:
            return None
        init_segment = parsed[0]
        init_hit += parsed[1]
        parsed = _parse_mov(data, init_hit)
        if parsed is None:
            return None
        init_num_bytes = parsed[0]
        init_start = _mem_to_file(init_address | (init_segment << 16), base)
        init_range = RollingRange(init_start, init_start + init_num_bytes - 1)

    # checksum value addresses + optional multirange store
    checksum_map: dict[int, int] = {}
    multirange_store: int | None = None
    current = saved_current
    stop = current + 2048
    while current < stop:
        hit = _locate_pattern(data, _PAT_CKSM_VALUE, _PAT_CKSM_VALUE_MASK, current, stop, 2)
        if hit < 0:
            break
        idx_addr, sizes = _find_prev_instruction_sequence_start(
            data, hit, hit - 64, ["MOVB", "CMPB"],
        )
        if idx_addr > 0:
            idx_addr += sizes[0]
            parsed = _parse_cmpb(data, idx_addr)
            if parsed is None:
                return None
            value_index = parsed[0]
            if value_index in checksum_map:
                return None
            parsed = _parse_mov(data, hit)
            if parsed is None:
                return None
            address = parsed[0]
            parsed = _parse_mov(data, hit + parsed[1])
            if parsed is None:
                return None
            segment = parsed[0]
            checksum_map[value_index] = address | (segment << 16)
            current = hit + len(_PAT_CKSM_VALUE)
        elif is_using_dual:
            parsed = _parse_mov(data, hit)
            if parsed is None:
                return None
            address = parsed[0]
            parsed = _parse_mov(data, hit + parsed[1])
            if parsed is None:
                return None
            segment = parsed[0]
            multirange_store = _mem_to_file(address | (segment << 16), base)
            break
        else:
            return None

    if not checksum_map:
        return None

    # address ranges (M-box pattern, C-box fallback)
    address_range_map: dict[int, RollingRange] = {}
    transition_map: dict[int, int] = {}
    current = saved_current
    stop = current + 2048
    while current < stop:
        hit = _locate_pattern(data, _PAT_RANGES_M, _PAT_RANGES_M_MASK, current, stop, 2)
        if hit < 0:
            break
        parsed = _parse_mov(data, hit)
        if parsed is None:
            break
        range_start_address = parsed[0]
        parsed = _parse_mov(data, hit + parsed[1])
        if parsed is None:
            break
        range_start_segment = parsed[0]
        hit += len(_PAT_RANGES_M) - 8
        parsed = _parse_mov(data, hit)
        if parsed is None:
            break
        range_end_address = parsed[0]
        parsed = _parse_mov(data, hit + parsed[1])
        if parsed is None:
            break
        range_end_segment = parsed[0]
        idx_addr, sizes = _find_prev_instruction_sequence_start(
            data, hit, hit - 64, ["MOVB", "CMPB"],
        )
        if idx_addr <= 0:
            return None
        idx_addr += sizes[0]
        parsed = _parse_cmpb(data, idx_addr)
        if parsed is None:
            return None
        index = parsed[0]
        if index in transition_map or index in address_range_map:
            return None
        next_idx_addr, _ = _find_next_instruction(data, hit, "MOVB")
        if next_idx_addr <= 0:
            return None
        parsed = _parse_movb(data, next_idx_addr)
        if parsed is None:
            return None
        next_index = parsed[0]
        start = _mem_to_file(range_start_address | (range_start_segment << 16), base)
        end = _mem_to_file(range_end_address | (range_end_segment << 16), base)
        address_range_map[index] = RollingRange(start, end)
        transition_map[index] = next_index
        current = hit + len(_PAT_RANGES_M)

    if not address_range_map:
        current = saved_current
        stop = current + 2048
        while current < stop:
            hit = _locate_pattern(data, _PAT_RANGES_C, _PAT_RANGES_C_MASK, current, stop, 2)
            if hit < 0:
                break
            hit += len(_PAT_RANGES_C)
            parsed = _parse_add(data, hit)
            if parsed is None:
                break
            range_start_address = parsed[0]
            hit += parsed[1]
            parsed = _parse_addc(data, hit)
            if parsed is None:
                break
            range_segment = parsed[0]
            hit, _ = _find_next_instruction(data, hit, "JMPR")
            if hit < 0:
                break
            hit += 2
            parsed = _parse_cmp(data, hit)
            if parsed is None:
                break
            range_end_address = parsed[0]
            hit, _ = _find_next_instruction(data, hit, "MOV")
            if hit < 0:
                break
            parsed = _parse_mov(data, hit)
            if parsed is None:
                break
            range_end_address = (range_end_address + parsed[0]) & 0xFFFF
            idx_addr, sizes = _find_prev_instruction_sequence_start(
                data, hit, hit - 64, ["MOVB", "CMPB", "JMPR"],
            )
            if idx_addr <= 0:
                return None
            idx_addr += sizes[0]
            parsed = _parse_cmpb(data, idx_addr)
            if parsed is None:
                return None
            index = parsed[0]
            if index in transition_map or index in address_range_map:
                return None
            next_idx_addr, _ = _find_next_instruction(data, hit, "MOVB")
            if next_idx_addr <= 0:
                return None
            parsed = _parse_movb(data, next_idx_addr)
            if parsed is None:
                return None
            next_index = parsed[0]
            start = _mem_to_file(range_start_address | (range_segment << 16), base)
            end = _mem_to_file(range_end_address | (range_segment << 16), base)
            address_range_map[index] = RollingRange(start, end)
            transition_map[index] = next_index
            current = hit + len(_PAT_RANGES_C)

        if not address_range_map:
            return None

    # group ranges per checksum slot by following the transition map
    ranges_map: dict[int, list[RollingRange]] = {}
    for range_index in address_range_map:
        current_index = range_index
        while True:
            if current_index not in transition_map:
                return None
            next_index = transition_map[current_index]
            if next_index in checksum_map:
                ranges_map.setdefault(next_index, []).append(address_range_map[range_index])
                break
            current_index = next_index

    if any(key not in ranges_map for key in checksum_map):
        return None

    # verify each slot — dual-checksum firmware CHAINS the rolling state
    # across slots (init range first, then each slot continues from the
    # previous slot's checksum); non-dual firmware resets per slot.
    entries: list[RollingChecksumEntry] = []
    computed = 0xFFFFFFFF
    if init_range is not None:
        computed = rolling_checksum(data, seed_table_offset, [init_range], computed)
    for key, store_addr in checksum_map.items():
        store = _mem_to_file(store_addr, base)
        ranges = ranges_map[key]
        if init_range is None:
            computed = 0xFFFFFFFF
        computed = rolling_checksum(data, seed_table_offset, ranges, computed)
        stored = struct.unpack_from("<I", data, store)[0] if store + 4 <= n else 0
        expected_inverted = (~computed) & 0xFFFFFFFF
        entries.append(
            RollingChecksumEntry(
                store_offset=store,
                ranges=tuple(ranges),
                init_range=init_range,
                stored=stored,
                expected=expected_inverted,
                status="ok" if stored == expected_inverted else "stale",
            )
        )
    return entries


def detect_me7_multirange(
    data: bytes, base: int = _ME7_FLASH_BASE,
) -> MultiRangeChecksumInfo | None:
    """Port of NefMoto DetectMultiRangeChecksum (standalone variant used
    by dual-checksum firmware)."""
    n = len(data)
    ranges: list[RollingRange] = []
    last_block_end = 0
    current = 0
    while 0 <= current < n:
        current = _locate_pattern(data, _PAT_MR_FUNC, _PAT_MR_FUNC_MASK, current, n, 2)
        if current < 0:
            break
        mov_off, _ = _find_next_instruction(data, current, "MOV")
        if mov_off < 0:
            return None
        parsed = _parse_mov(data, mov_off)
        if parsed is None:
            return None
        range_start_address = parsed[0]
        mov_off += parsed[1]
        parsed = _parse_mov(data, mov_off)
        if parsed is None:
            return None
        range_start_segment = parsed[0]
        addc_off, size = _find_next_instruction(data, mov_off, "ADDC")
        if addc_off < 0:
            return None
        addc_off += size
        parsed = _parse_mov(data, addc_off)
        if parsed is None:
            return None
        range_end_address = parsed[0]
        addc_off += parsed[1]
        parsed = _parse_mov(data, addc_off)
        if parsed is None:
            return None
        range_end_segment = parsed[0]
        last_block_end = addc_off + parsed[1]
        start = _mem_to_file(range_start_address | (range_start_segment << 16), base)
        end = _mem_to_file(range_end_address | (range_end_segment << 16), base)
        ranges.append(RollingRange(start, end))
        current = last_block_end

    if not ranges:
        return None

    value_off = _locate_pattern(
        data, _PAT_MR_VALUE, _PAT_MR_VALUE_MASK, last_block_end, last_block_end + 256, 2,
    )
    if value_off < 0:
        return None
    mov_off, _ = _find_next_instruction(data, value_off, "MOV")
    if mov_off < 0:
        return None
    parsed = _parse_mov(data, mov_off)
    if parsed is None:
        return None
    value_address = parsed[0]
    mov_off += parsed[1]
    parsed = _parse_mov(data, mov_off)
    if parsed is None:
        return None
    value_segment = parsed[0]
    store = _mem_to_file(value_address | (value_segment << 16), base)

    computed = multirange_checksum(data, ranges)
    stored = struct.unpack_from("<I", data, store)[0] if store + 4 <= n else 0
    inv_stored = struct.unpack_from("<I", data, store + 4)[0] if store + 8 <= n else 0
    if stored != ((~inv_stored) & 0xFFFFFFFF):
        status = "pair_mismatch"
    elif stored == computed:
        status = "ok"
    else:
        status = "stale"
    return MultiRangeChecksumInfo(
        store_offset=store,
        ranges=tuple(ranges),
        stored=stored,
        inv_stored=inv_stored,
        expected=computed,
        status=status,
    )
