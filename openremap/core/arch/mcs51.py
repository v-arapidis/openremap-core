"""
MCS-51 (8051) reference collection for the code-reference (xref) signal.

Thin Python adapter over the Rust decoder (``openremap._rust.mcs51_references`` /
``mcs51_walk``, see ``_rs/src/arch/mcs51.rs``).  There is deliberately NO
Python decoder — the disassembly is Rust-only (the C166 one's sibling).

Unlike C166 — whose references are 16-bit operands resolved through a DPP
window — the 8051 reaches tables through the DPTR register.  The only
16-bit-immediate load is ``MOV DPTR, #data16`` (0x90, big-endian), which the
Rust decoder collects as the reference.  The address space is flat (address
== file offset for the ≤64 KB 8051 images), so the references are used as-is
with no load-base translation.
"""

from __future__ import annotations

from openremap._rust import (  # type: ignore[import-untyped]
    mcs51_references as _rust_mcs51_references,
    mcs51_walk as _rust_mcs51_walk,
)


def collect_references(
    data: bytes, regions: list[tuple[int, int]]
) -> tuple[list[tuple[int, int]], int]:
    """``((addr16, insn_addr) pairs, instruction_count)`` for *regions*.

    The Rust decoder returns the 16-bit ``MOV DPTR, #data16`` immediates
    (big-endian) plus the number of decoded instructions.
    """
    refs, insn_count = _rust_mcs51_references(data, [(s, e) for s, e in regions])
    return [(int(addr), int(off)) for addr, off in refs], int(insn_count)


def walk(data: bytes, regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``(insn_offset, length)`` for every decoded instruction in *regions*."""
    return [
        (int(off), int(size))
        for off, size in _rust_mcs51_walk(data, [(s, e) for s, e in regions])
    ]
