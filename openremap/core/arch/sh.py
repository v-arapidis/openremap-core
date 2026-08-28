"""
SuperH reference extraction for the code-reference (xref) signal.

capstone's SH memory-operand detail is unavailable (empty operand
structs), so the absolute-memory form ``mov.l/mov.w ADDR, rN`` is matched
on the rendered operand text.  Register-indirect / index forms
(``@rN``, ``@(rN,rM)``, ``@(disp,pc)``) are not matched — conservative
on purpose.
"""

from __future__ import annotations

import re

#: SuperH absolute-memory text forms: "mov.l 0x1234,r4" / "mov.w 0x1234,r4"
#: (capstone's SH operand detail is unavailable, so the absolute-address
#: form is matched on the rendered operand text).
_SH_ABS_RE = re.compile(r"^(mov\.l|mov\.w)\s+(0x[0-9a-fA-F]+)\s*,")


def _extract_sh(insns: iter) -> list[tuple[int, int]]:
    """SuperH: absolute ``mov.l/mov.w ADDR, rN`` text operands.

    capstone's SH memory-operand detail is unavailable (empty operand
    structs), so the absolute form is matched on the rendered text.
    Register-indirect / index forms (``@rN``, ``@(rN,rM)``, ``@(disp,pc)``)
    are not matched — conservative on purpose.
    """
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0:
            continue
        m = _SH_ABS_RE.match(insn.op_str)
        if m:
            out.append((int(m.group(2), 16), insn.address))
    return out
