"""
Motorola M680X / M68K reference extraction for the xref signal.

Two capstone-backed extractors for the legacy Motorola families the arch
census (notes/arch/census.md) classified as capstone-backable:

- **M680X** (68HC11 / 6800 / 6809) — M1.x, M3.x, MP3.x/MP7.2 (68HC11),
  LH-Jetronic (6800/6802).  Absolute (extended) operands render as
  ``$xxxx`` (or ``>$xxxx``); direct-page (``$xx``) and immediate (``#$xx``)
  are not matched, and branches are PC-relative (excluded) — so only
  statically-resolvable memory references are collected.
- **M68K** (68000 / CPU32 / 68332) — M1.5.5/M1.55, Marelli IAW 4LV.
  Absolute operands render as ``$xxxx``/``$xxxxxxxx`` with the size in the
  mnemonic (``move.w $1234, d0``); immediates are ``#$x``.  Branch targets
  are PC-relative (excluded).

Both are conservative on purpose — same contract as ``sh.py``: only
self-contained absolute addresses, presence-only signal.
"""

from __future__ import annotations

import re

# M680X: $xxxx (extended absolute) — exclude immediate (#$) and a following
# index suffix (",x"/",y").  Direct-page $xx (2 hex digits) is excluded by
# requiring exactly 4 digits.
_M680X_ABS_RE = re.compile(r"(?<!#)\$(?:>)?([0-9a-fA-F]{4})\b(?!\s*,)")

_M680X_MEM_MNEMONICS = frozenset({
    "ldaa", "ldab", "ldd", "lds", "ldx", "ldy",
    "staa", "stab", "std", "sts", "stx", "sty",
    "adda", "addb", "addd", "adca", "adcb",
    "suba", "subb", "subd", "sbca", "sbcb",
    "cmpa", "cmpb", "cpd", "cpx", "cpy",
    "anda", "andb", "oraa", "orab", "eora", "eorb", "bita", "bitb",
    "jsr", "jmp",
})

# M68K: $xxxx / $xxxxxxxx absolute (immediates are #$x).  The size suffix
# lives in the mnemonic (move.w / movea.l / …), so the operand is just the
# address.
_M68K_ABS_RE = re.compile(r"(?<!#)\$([0-9a-fA-F]+)")

_M68K_MEM_MNEMONICS = frozenset({
    "move", "movea", "lea", "pea", "add", "adda", "addi", "addq",
    "sub", "suba", "subi", "subq", "cmp", "cmpa", "cmpi",
    "and", "andi", "or", "ori", "eor", "eori", "tst", "clr", "neg", "not",
    "jsr", "jmp", "btst",
})


def _extract_m680x(insns: iter) -> list[tuple[int, int]]:
    """M680X: absolute ``$xxxx`` memory operands of load/store/arith forms."""
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0 or insn.mnemonic not in _M680X_MEM_MNEMONICS:
            continue
        for m in _M680X_ABS_RE.finditer(insn.op_str):
            out.append((int(m.group(1), 16), insn.address))
    return out


def _extract_m68k(insns: iter) -> list[tuple[int, int]]:
    """M68K: absolute ``$xxxx``/``$xxxxxxxx`` memory operands."""
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0:
            continue
        if insn.mnemonic.split(".")[0] not in _M68K_MEM_MNEMONICS:
            continue
        for m in _M68K_ABS_RE.finditer(insn.op_str):
            out.append((int(m.group(1), 16), insn.address))
    return out
