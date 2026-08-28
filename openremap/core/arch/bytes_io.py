"""
Shared byte readers — the checksum domain's small LE/BE u16/u32 readers
and range-sum/CRC helpers.

Dedup of the helpers that were duplicated across
``services/checksums/ironfelix.py`` (lines 105–215), ``ms43.py``
(lines 108/196) and ``nefmoto.py`` (line 133) — same functions, moved
here verbatim with their exact semantics.  Two families:

- **Non-nullable readers** (``u16le``/``u16be``/``u32le``) return ``int``
  and raise on out-of-bounds.  For callers that pre-check bounds or
  guarantee valid offsets (ironfelix, nefmoto).
- **Nullable readers** (``u16le_opt``/``u16be_opt``/``u32le_opt``) return
  ``int | None`` — out-of-bounds yields ``None``.  For callers that treat
  OOB as "absent" (ms43's descriptor reads).

The two families must NOT be merged: ms43 branches on ``None``, ironfelix
and nefmoto never pass OOB offsets.  ``sum8``/``sum16le``/``crc32`` stay
in Python because their semantics deliberately differ from the Rust
algorithms (see their docstrings) — the Rust ``sum16le_acc32``/``sum8``
algos handle odd tails and masking differently.
"""

from __future__ import annotations

import struct

from openremap._rust import checksum_compute  # type: ignore[import-untyped]


def u16le(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u16be(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def u32le(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u16le_opt(data: bytes, off: int) -> int | None:
    if off < 0 or off + 2 > len(data):
        return None
    return data[off] | (data[off + 1] << 8)


def u16be_opt(data: bytes, off: int) -> int | None:
    if off < 0 or off + 2 > len(data):
        return None
    return (data[off] << 8) | data[off + 1]


def u32le_opt(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, off)[0]


def sum8(data: bytes, s: int, e_incl: int) -> int:
    """Byte sum into a u32, end INCLUSIVE.

    Note: the Rust ``sum8`` algo masks to 8 bits; the reference
    accumulates the full u32 byte sum, so use CPython's C-speed
    ``sum()`` over the slice instead.
    """
    return sum(data[s : e_incl + 1]) & 0xFFFFFFFF


def sum16le(data: bytes, s: int, e_excl: int) -> int:
    """LE u16 words, u32 accumulator.

    End EXCLUSIVE with the reference's C-loop semantics: words start at
    even offsets i < end and read bytes i and i+1, so an odd region
    length pairs the trailing byte with the byte AT ``end`` (the
    reference reads one byte past the region).  A missing final byte
    reads as 0.  (The Rust ``sum16le_acc32`` algo handles odd tails
    differently, so this stays in Python.)
    """
    total = 0
    for i in range(s, e_excl, 2):
        word = data[i]
        if i + 1 < len(data):
            word |= data[i + 1] << 8
        total = (total + word) & 0xFFFFFFFF
    return total


def sumb_pages(data: bytes, s: int, e_excl: int) -> int:
    """For every 0x2000 page in [s, e_excl): first u16 LE word + last
    u16 LE word, u32 accumulator."""
    total = 0
    i = s
    while i < e_excl:
        total += u16le(data, i) + u16le(data, i + 0x1FFE)
        i += 0x2000
    return total & 0xFFFFFFFF


def crc32(data: bytes, s: int, e_incl: int) -> int:
    """CRC-32/IEEE over [s, e_incl] (end inclusive)."""
    return checksum_compute(data, [(10, 0xFFFFFFFF, s, e_incl + 1)])[0] ^ 0xFFFFFFFF


def crc32_cont(data: bytes, s: int, e_incl: int, prev: int) -> int:
    """Continue a finished CRC over the next zone (end inclusive)."""
    return (
        checksum_compute(data, [(10, prev ^ 0xFFFFFFFF, s, e_incl + 1)])[0]
        ^ 0xFFFFFFFF
    )


def find_all(data: bytes, needle: bytes, start: int = 0) -> list[int]:
    out: list[int] = []
    off = data.find(needle, start)
    while off != -1:
        out.append(off)
        off = data.find(needle, off + 1)
    return out
