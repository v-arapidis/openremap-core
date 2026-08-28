"""
TriCore reference extraction for the code-reference (xref) signal.

Extracts the compiler's canonical absolute-address materialisation pair
from capstone-rendered operands (``detail=False`` — measured ~2.6x faster
than operand-detail inspection on the real 4 MB EDC17 corpus,
2026-08-26): ``movh.a aN, #hi`` immediately followed by
``lea aN, [aN]disp``.  Rendered-operand parsing keeps this module free of
capstone-implementation coupling beyond the mnemonic/operand text.

a0-resolution (cheap-wins item 4, 2026-08-27): TriCore also materialises
a global base register ``a0`` once at boot (``movh.a a0, #hi`` immediately
followed by ``lea a0, [a0]disp``) and then addresses data through
``lea aN, [a0]disp``.  :func:`_find_a0` recovers that base from the same
rendered-operand pair; :func:`_extract_tricore_a0` resolves the
``[a0]``-relative accesses against it.  :func:`_extract_tricore_pass` does
the whole job (self-contained pairs + a0 init + buffered ``[a0]`` leas) in
a single decode walk so the collector never disassembles the binary twice.
"""

from __future__ import annotations

import re

#: TriCore address-materialisation pair, rendered operand text:
#:   movh.a aN, #0xd002     (high word of a 32-bit absolute address)
#:   lea    aN, [aN]#-0x4f6c (signed displacement on the same register)
#: (``op_str`` holds only the operands — match against the mnemonic first,
#: then parse the operand text with these.)
_MOVHA_RE = re.compile(r"^(\S+),\s*(#?-?0x[0-9a-fA-F]+|#?-?\d+)$")
_LEA_RE = re.compile(r"^(\S+),\s*\[(\S+)\](#-?0x[0-9a-fA-F]+|#-?\d+|0x[0-9a-fA-F]+|\d+)$")


def _parse_num(s: str) -> int:
    """Parse capstone-rendered immediate text (``0x1234`` / ``-0x4f6c`` /
    ``#8`` / ``-8``) into an int."""
    t = s.lstrip("#")
    return int(t, 16) if ("0x" in t or "0X" in t) else int(t)


def _pair_target(prev: object, insn: object) -> tuple[int, str] | None:
    """Self-contained ``movh.a aN, #hi`` + ``lea aN, [aN]disp`` pair.

    Returns ``(target, aN)`` for the canonical absolute-address
    materialisation, or ``None``.  ``prev`` must be the ``movh.a`` insn
    immediately preceding ``insn`` — the caller owns adjacency tracking
    (this helper is pure match + math).  ``disp`` is signed (negative
    displacements are normal — calibration bases sit below the high word).
    """
    if prev is None or prev.mnemonic != "movh.a" or insn.mnemonic != "lea":
        return None
    m1 = _MOVHA_RE.match(prev.op_str)
    m2 = _LEA_RE.match(insn.op_str)
    if not (m1 and m2 and m1.group(1) == m2.group(2)):
        return None
    target = ((_parse_num(m1.group(2)) << 16) + _parse_num(m2.group(3))) & 0xFFFFFFFF
    return target, m1.group(1)


def _extract_tricore(insns: iter) -> list[tuple[int, int]]:
    """TriCore: ``movh.a aN, #hi`` + next-insn ``lea aN, [aN]disp`` pairs.

    Returns ``[(target, insn_address)]`` — the 32-bit absolute address
    materialised by the pair.  ``disp`` is signed (negative displacements
    are normal — calibration bases sit below the high word).

    Rendered-operand parsing (capstone ``detail=False``) — measured on the
    real 4 MB EDC17 corpus: ~2.6x faster than ``detail=True`` operand
    inspection for the same signal (2026-08-26).
    """
    out: list[tuple[int, int]] = []
    prev: object = None
    for insn in insns:
        if insn.id == 0:  # skipdata placeholder — never an operand source
            prev = None
            continue
        hit = _pair_target(prev, insn)
        if hit is not None:
            out.append((hit[0], prev.address))
        prev = insn
    return out


def _find_a0(insns: iter) -> int | None:
    """Boot-time global base register ``a0``.

    TriCore code sets ``a0`` once at boot (``movh.a a0, #hi`` immediately
    followed by ``lea a0, [a0]#disp`` → ``base_a0 = (hi << 16) + disp``,
    masked to 32 bits) and then addresses data through
    ``lea aN, [a0]disp``.  The init is one-shot startup code, so the first
    canonical pair wins.  Returns ``None`` when no canonical init is found
    — presence-only: the caller emits nothing extra in that case.
    """
    prev: object = None
    for insn in insns:
        if insn.id == 0:
            prev = None
            continue
        hit = _pair_target(prev, insn)
        if hit is not None and hit[1] == "a0":
            return hit[0]
        prev = insn
    return None


def _extract_tricore_a0(insns: iter, a0_base: int) -> list[tuple[int, int]]:
    """TriCore: ``lea aN, [a0]disp`` → ``(a0_base + disp) & 0xFFFFFFFF``.

    Resolves the a0-relative accesses against the boot-time base from
    :func:`_find_a0`.  Only the base-register form ``[a0]`` is matched;
    ``aN == a0`` is excluded — the init pair's own ``lea a0, [a0]`` is
    already reported as a self-contained pair by :func:`_extract_tricore`.
    ``disp`` is signed (negative displacements are normal).
    """
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0:
            continue
        if insn.mnemonic == "lea":
            m = _LEA_RE.match(insn.op_str)
            if m and m.group(2) == "a0" and m.group(1) != "a0":
                out.append(((a0_base + _parse_num(m.group(3))) & 0xFFFFFFFF, insn.address))
    return out


def _extract_tricore_pass(insns: iter) -> tuple[list[tuple[int, int]], int | None, list]:
    """Single-walk TriCore extraction: pairs + a0 init + buffered ``[a0]`` leas.

    Returns ``(pairs, a0_base, a0_lea_insns)``:
    - ``pairs`` — self-contained ``movh.a aN,#hi`` + ``lea aN,[aN]disp``
      targets (the existing signal, unchanged);
    - ``a0_base`` — boot-time ``a0`` base (:func:`_find_a0`), or ``None``
      when absent;
    - ``a0_lea_insns`` — buffered capstone insn objects for every
      ``lea aN,[a0]disp`` with ``aN != a0``.  The caller resolves them with
      :func:`_extract_tricore_a0` *after* the whole pass — the init may sit
      in a different region than the accesses it serves, and the collector
      must not decode the binary twice.

    One decode walk serves all three signals (streaming — callers must not
    re-decode the same bytes for a separate a0 scan).
    """
    pairs: list[tuple[int, int]] = []
    a0_base: int | None = None
    a0_lea_insns: list = []
    prev: object = None
    for insn in insns:
        if insn.id == 0:
            prev = None
            continue
        hit = _pair_target(prev, insn)
        if hit is not None:
            target, reg = hit
            pairs.append((target, prev.address))
            if a0_base is None and reg == "a0":
                a0_base = target
        if insn.mnemonic == "lea":
            m = _LEA_RE.match(insn.op_str)
            if m and m.group(2) == "a0" and m.group(1) != "a0":
                a0_lea_insns.append(insn)
        prev = insn
    return pairs, a0_base, a0_lea_insns
