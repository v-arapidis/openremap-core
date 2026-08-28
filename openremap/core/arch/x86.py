"""
x86 reference extraction for the code-reference (xref) signal.

Absolute memory operands ``[0x…]`` (no base register).  Used by tests as
the architecture-independent mechanics path; also a generic fallback for
future absolute-addressing arches.
"""

from __future__ import annotations

import re

#: x86 absolute memory operand: "eax, dword ptr [0x1234]".  Bracket content
#: must be a bare hex literal (register forms like ``[ebx]`` / ``[ebx+0x10]``
#: are not matched).  Used by tests as the architecture-independent mechanics
#: path; also a generic fallback for future absolute-addressing arches.
_X86_ABS_RE = re.compile(r"\[(0x[0-9a-fA-F]+)\]")


def _extract_x86(insns: iter) -> list[tuple[int, int]]:
    """x86: absolute memory operands ``[0x…]`` (no base register)."""
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0:
            continue
        m = _X86_ABS_RE.search(insn.op_str)
        if m:
            out.append((int(m.group(1), 16), insn.address))
    return out
