"""
PowerPC reference extraction for the xref signal.

Marelli MJD 6JF (and the ``mpc555`` bin in ``tests/data/ECUs/Unknown/``)
run PowerPC.  PPC reaches absolute data two ways — a load/store with ``r0``
as the base register (``r0`` reads as 0, so ``lwz rD, d(r0)`` addresses the
absolute ``d``) or a ``lis``/``ori`` address-materialisation pair.  Only
the r0-based displacement form is matched: simple, statically resolvable,
and the same "self-contained absolute address" contract as the other
extractors.  The ``lis``/``ori`` pair needs register tracking (out of
scope, like every other extractor here).

Not corpus-verified (no MJD 6JF binary is present; the ``mpc555`` file in
``Unknown/`` is not a registered family) — best-effort, presence-only.
"""

from __future__ import annotations

import re

# lwz/lhz/… rD, <disp>(r0) — capstone renders the displacement as hex
# (0x…) for the forms seen on the mpc555 corpus bin.
_PPC_R0_RE = re.compile(r"0x([0-9a-fA-F]+)\(r0\)")

_PPC_MEM_MNEMONICS = frozenset({
    "lwz", "lhz", "lha", "lbz", "stw", "sth", "stb",
    "lfs", "lfd", "stfs", "stfd",
})


def _extract_ppc(insns: iter) -> list[tuple[int, int]]:
    """PowerPC: load/store ``d(r0)`` absolute displacements."""
    out: list[tuple[int, int]] = []
    for insn in insns:
        if insn.id == 0 or insn.mnemonic not in _PPC_MEM_MNEMONICS:
            continue
        for m in _PPC_R0_RE.finditer(insn.op_str):
            out.append((int(m.group(1), 16), insn.address))
    return out
