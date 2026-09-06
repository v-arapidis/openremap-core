"""
openremap.core.arch.routine — read a code routine as pseudo-code.

The bytes→dict ``routine`` core: auto-detect the CPU from the binary's
identity, validate the decoder key, and render the instruction stream at an
offset.  Pure bytes/dict in-out; the ``routine`` RPC handler (path I/O) stays
in the API gate.  A missing decoder or unsupported arch raises ``ValueError``
here — the gate translates it to ``GuardError``.
"""

from __future__ import annotations

from openremap.core.arch import arch_for_family
from openremap.core.arch.pseudocode import render_routine
from openremap.core.services.identify.identifier import identify_ecu

_ARCH_KEYS = ("c166", "tricore", "sh", "x86", "m680x", "m68k", "ppc")


def routine_binary(
    data: bytes,
    filename: str,
    offset: int,
    *,
    arch: str | None = None,
    before: int = 8,
    after: int = 60,
) -> dict:
    """Decode the instruction stream at *offset*, returning lines + arch."""
    key = arch
    if key is None:
        ident = identify_ecu(data=data, filename=filename)
        info = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
        if info is None:
            fam = ident.get("ecu_family") or "unknown"
            raise ValueError(
                f"No decoder for family '{fam}' — pass --arch to force one "
                f"(supported: {', '.join(_ARCH_KEYS)})."
            )
        key = info[0]

    if key not in _ARCH_KEYS:
        raise ValueError(f"Unsupported arch {key!r}.")

    lines = render_routine(data, offset, arch=key, before=before, after=after)
    return {"arch": key, "offset": offset, "lines": lines}
