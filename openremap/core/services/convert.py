"""
Binary-image decoding — Intel HEX / Motorola S-Record / raw binary.

Real Intel HEX (``.hex`` / ``.ihex``) and Motorola S-Record (``.s19`` /
``.srec`` / ``.mot``) files are text: each record carries a byte count, an
absolute address, data, and a per-record checksum.  Treating them as raw
byte dumps yields garbage.  This module sniffs file *content* and decodes
real HEX/SREC into a flat ``bytes`` image; anything else is returned
untouched (the raw-binary path — Subaru/RomRaider dumps ship as raw
binaries named ``.hex`` and must keep working byte-identically).

Pure ``bytes``-in/``bytes``-out — no I/O, no file paths — so it slots into
the "services accept bytes" rule and is unit-testable without files.
Backed by ``bincopy`` (MIT), which validates per-record checksums.

Policy decisions (documented for portability):
  - Gaps between segments are filled with ``0xFF`` (erased flash).  This is
    also ``bincopy``'s default padding — we rely on it, base-normalised to
    the minimum address automatically.
  - A file whose first byte looks like ``:`` / ``S`` but fails to parse is
    treated as raw binary *with a warning* (a raw dump can legitimately
    start with 0x3A or 0x53); a file that structurally looks like the
    format (hex record shape) but is corrupt raises ``ValueError``.  Pass
    ``force`` to skip the sniff/fallback entirely.
  - Absurd address spans (> 256 MB) are refused to avoid a pathological
    ``as_binary()`` allocation.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

import bincopy

#: Refuse images whose address span exceeds this (guard against a
#: pathological single-byte record at 0xFFFFFFFF materialising 4 GB).
MAX_IMAGE_SPAN = 256 * 1024 * 1024

#: Record-shape sniff: does the first line look like a real HEX/SREC record?
_LOOKS_LIKE = {
    "ihex": re.compile(r"^:[0-9A-Fa-f]{8,}"),
    "srec": re.compile(r"^S[0-9][0-9A-Fa-f]{8,}"),
}


@dataclass
class DecodeResult:
    """Result of decoding a binary-image file."""

    data: bytes
    format: str  # "ihex" | "srec" | "binary"
    warnings: List[str] = field(default_factory=list)
    address_min: Optional[int] = None  # absolute, inclusive
    address_max: Optional[int] = None  # absolute, exclusive
    segments: int = 0  # 0 for raw binary; >1 means gaps were 0xFF-filled


def decode_image(raw: bytes, force: Optional[str] = None) -> DecodeResult:
    """Decode ``raw`` file bytes into a flat binary image.

    ``force`` skips content sniffing: ``"ihex"`` / ``"srec"`` parse as that
    format strictly (corrupt input raises), ``"bin"`` returns the bytes
    untouched, ``None`` (default) sniffs content with raw fallback.

    Raises ``ValueError`` for a structurally-plausible-but-corrupt HEX/SREC
    file (checksum failure, no data, span beyond the sanity limit).  Raw
    input is never an error — it is returned unchanged.
    """
    if force == "bin":
        return DecodeResult(raw, "binary")

    stripped = raw.lstrip(b" \t\r\n\v\f")

    fmt = force
    if fmt is None:
        if stripped[:1] == b":":
            fmt = "ihex"
        elif stripped[:1] == b"S" and stripped[1:2] in b"0123456789":
            fmt = "srec"
        else:
            return DecodeResult(raw, "binary")

    try:
        bf = bincopy.BinFile()
        adder = bf.add_ihex if fmt == "ihex" else bf.add_srec
        adder(stripped.decode("latin-1"))
    except (bincopy.Error, ValueError) as exc:
        # bincopy raises bincopy.Error for bad checksums but a plain
        # ValueError (bytearray.fromhex) for non-hex garbage.
        if force is not None or _looks_like(fmt, stripped):
            raise ValueError(f"invalid {fmt} data: {exc}") from exc
        return DecodeResult(
            raw,
            "binary",
            [f"file starts like {fmt} but did not parse — treated as raw binary"],
        )

    if not bf.segments:
        raise ValueError(f"no data found in {fmt} file")
    span = bf.maximum_address - bf.minimum_address
    if span > MAX_IMAGE_SPAN:
        raise ValueError(
            f"{fmt} file address span ({span} bytes) exceeds the "
            f"{MAX_IMAGE_SPAN}-byte sanity limit"
        )
    data = bytes(bf.as_binary())

    warnings = (
        [f"{len(bf.segments)} data segments — gaps filled with 0xFF"]
        if len(bf.segments) > 1
        else []
    )
    return DecodeResult(
        data,
        fmt,
        warnings,
        bf.minimum_address,
        bf.maximum_address,
        len(bf.segments),
    )


def _looks_like(fmt: str, head: bytes) -> bool:
    """True when the file's first line has the record shape of ``fmt``.

    Used to distinguish "real HEX/SREC that is corrupt" (loud error) from
    "raw dump that happens to start with ':'/'S'" (raw fallback).
    """
    first_line = head.splitlines()[0] if head.splitlines() else b""
    return bool(_LOOKS_LIKE[fmt].match(first_line.decode("latin-1")))


def encode_ihex(data: bytes, base: int = 0) -> str:
    """Encode ``data`` as an Intel HEX text string starting at ``base``."""
    bf = bincopy.BinFile()
    bf.add_binary(data, address=base)
    return bf.as_ihex()


def encode_srec(data: bytes, base: int = 0) -> str:
    """Encode ``data`` as an S-Record text string starting at ``base``."""
    bf = bincopy.BinFile()
    bf.add_binary(data, address=base)
    return bf.as_srec()
