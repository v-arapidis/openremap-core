"""
MCS-96 (8096) reference collection for the code-reference (xref) signal.

Thin Python adapter over the Rust decoder (``openremap._rust.mcs96_references`` /
``mcs96_walk``, see ``_rs/src/arch/mcs96.rs``).  There is deliberately NO
Python decoder — the disassembly is Rust-only.

The 8096 reaches data through a register file (R0–R255, R0 hardwired 0) plus
an offset, so the 16-bit data references are the ``ld reg,#imm16`` immediates
(the base a later indexed access uses) and the ``ljmp``/``lcall`` absolute
targets.  Address space is flat (address == file offset for the ≤64 KB 8096
images), so the references are used as-is.
"""

from __future__ import annotations

from openremap._rust import (  # type: ignore[import-untyped]
    mcs96_references as _rust_mcs96_references,
    mcs96_walk as _rust_mcs96_walk,
)


def collect_references(
    data: bytes, regions: list[tuple[int, int]]
) -> tuple[list[tuple[int, int]], int]:
    """``((addr16, insn_addr) pairs, instruction_count)`` for *regions*.

    The Rust decoder returns the 16-bit ``ld reg,#imm16`` immediates and the
    ``ljmp``/``lcall`` targets (little-endian) plus the instruction count.
    """
    refs, insn_count = _rust_mcs96_references(data, [(s, e) for s, e in regions])
    return [(int(addr), int(off)) for addr, off in refs], int(insn_count)


def walk(data: bytes, regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``(insn_offset, length)`` for every decoded instruction in *regions*."""
    return [
        (int(off), int(size))
        for off, size in _rust_mcs96_walk(data, [(s, e) for s, e in regions])
    ]
