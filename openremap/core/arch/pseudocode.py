"""
Pseudo-code rendering — "read any routine without Ghidra".

The phrasebook front-end of the pseudo-decompiler: given a binary and a
file offset, render the surrounding instructions as one readable line each
(``0x1234  MOV  R0, 0x8100``), so the operator can see what a routine
does — "loads this address, adds to a running total, compares, jumps" —
without installing the ~1.5 GB Ghidra toolchain.

One shared entry point (:func:`render_routine`) + per-arch back-ends,
mirroring the ``collect_xrefs`` dispatch pattern in ``core/arch/refs.py``:

- **c166** — the Rust decoder (``c166_disasm``, mnemonic + operand
  emission added 0.8.x) via the ``core/arch/c166.py`` adapter.
- **tricore / sh / x86 / m680x / m68k / ppc** — capstone already
  disassembles these fully; a thin adapter formats its output into the
  same line shape.

Honest scope: a phrasebook, not Ghidra.  No
register renaming, no dataflow, no loop reconstruction — each instruction
renders as one readable line.  Memory operands are shown as the raw
logical addresses the decoder sees (C166 DPP-windowed 16-bit, capstone's
absolute operands); address resolution is a later concern.

Domain-neutral: accepts plain ``bytes`` and a file offset — no paths, no
hidden state (project pattern).
"""

from __future__ import annotations

from capstone import (  # type: ignore[import-untyped]
    CS_ARCH_M680X,
    CS_ARCH_M68K,
    CS_ARCH_PPC,
    CS_ARCH_SH,
    CS_ARCH_TRICORE,
    CS_ARCH_X86,
    CS_MODE_32,
    CS_MODE_BIG_ENDIAN,
    CS_MODE_M680X_6811,
    CS_MODE_M68K_000,
    CS_MODE_SH2,
    CS_MODE_TRICORE_160,
    Cs,
)

from openremap.core.arch import c166 as _c166
from openremap.core.services.maps.layout import code_regions_from_layout, segment

#: arch_key -> (capstone_arch, base_mode).  The c166 key is handled by the
#: Rust decoder instead (no capstone mapping exists).  ``m680x`` defaults to
#: the 68HC11 mode (the dominant M680X family; LH-Jetronic's 6800 is a close
#: subset — a mode override is a later refinement).
_CAPSTONE: dict[str, tuple[int, int]] = {
    "tricore": (CS_ARCH_TRICORE, CS_MODE_TRICORE_160),
    "sh": (CS_ARCH_SH, CS_MODE_SH2),
    "x86": (CS_ARCH_X86, CS_MODE_32),
    "m680x": (CS_ARCH_M680X, CS_MODE_M680X_6811),
    "m68k": (CS_ARCH_M68K, CS_MODE_M68K_000),
    "ppc": (CS_ARCH_PPC, CS_MODE_BIG_ENDIAN),
}


def _region_containing(
    regions: list[tuple[int, int]], offset: int
) -> tuple[int, int] | None:
    for s, e in regions:
        if s <= offset < e:
            return (s, e)
    return None


def render_routine(
    data: bytes,
    offset: int,
    arch: str | None = None,
    *,
    before: int = 8,
    after: int = 60,
) -> list[str]:
    """Render the instructions around *offset* as readable lines.

    Args:
        data:   Raw ECU binary content.
        offset: File offset (project convention) of the routine to show.
        arch:   Decoder key — ``"c166"``, ``"tricore"``, ``"sh"``,
                ``"x86"``, ``"m680x"``, ``"m68k"`` or ``"ppc"``.
                ``None`` → a hint line, no decode.
        before: Instructions to show before the target.
        after:  Instructions to show after the target.

    Returns one ``"  │ 0x…  mnemonic  operands"``-style line per
    instruction; the instruction nearest *offset* is marked ``>>``.
    """
    if arch is None:
        return [";; arch not specified — pass arch='c166'|'tricore'|'sh'|'x86'|'m680x'|'m68k'|'ppc'"]
    if arch == "c166":
        return _render_c166(data, offset, before, after)
    return _render_capstone(data, offset, arch, before, after)


def _render_c166(data: bytes, offset: int, before: int, after: int) -> list[str]:
    regions = code_regions_from_layout(segment(data))
    region = _region_containing(regions, offset)
    if region is None:
        return [f";; offset 0x{offset:X} is not inside a code region"]
    insns = _c166.disasm(data, [region])
    if not insns:
        return [";; no instructions decoded in the code region"]
    idx = min(range(len(insns)), key=lambda i: abs(insns[i][0] - offset))
    lo = max(0, idx - before)
    hi = min(len(insns), idx + after + 1)
    lines: list[str] = []
    for i in range(lo, hi):
        off, _size, m, op = insns[i]
        mark = ">>" if i == idx else "  "
        lines.append(f"{mark} {off:06X}  {m:<6} {op}".rstrip())
    return lines


def _render_capstone(
    data: bytes, offset: int, arch: str, before: int, after: int
) -> list[str]:
    cap = _CAPSTONE.get(arch)
    if cap is None:
        return [f";; unsupported arch {arch!r}"]
    arch_const, mode = cap
    regions = code_regions_from_layout(segment(data))
    region = _region_containing(regions, offset)
    if region is None:
        # fall back to a raw window around the offset (e.g. offset in a
        # region the segmenter did not label "code")
        region = (max(0, offset - 0x40), min(len(data), offset + 0x400))
    s, e = region
    md = Cs(arch_const, mode)
    md.skipdata = True
    insns = list(md.disasm(data[s:e], s))
    if not insns:
        return [";; no instructions decoded"]
    idx = min(range(len(insns)), key=lambda i: abs(insns[i].address - offset))
    lo = max(0, idx - before)
    hi = min(len(insns), idx + after + 1)
    lines: list[str] = []
    for i in range(lo, hi):
        insn = insns[i]
        mark = ">>" if i == idx else "  "
        lines.append(f"{mark} {insn.address:06X}  {insn.mnemonic:<6} {insn.op_str}".rstrip())
    return lines
