"""
Code-reference (xref) collection — the arch-domain core pass.

The engine step between map discovery (``scan_map_tables``, Rust) and
user-facing scoring/labelling: disassemble the flash **code regions** with
capstone and collect the byte offsets that real instructions statically
reference.  A table whose **data block** is referenced by code is almost
certainly a genuine calibration map — it receives a small score bonus and
carries evidence ("referenced by code at 0x…").  The score application
lives in the map domain (``services/maps/xrefs.py``); this module only
collects.

Design contract (see ``notes/arch/xrefs.md`` — the implementation plan):

- **Presence-only signal, never a penalty.**  Most ECU code reaches maps
  through base-register addressing that cannot be resolved statically
  (e.g. ``lea aN, [a0]disp`` with a runtime global base), so a *missing*
  reference proves nothing.  Absence never demotes a table.
- **Arch-gated and conservative.**  Only families with a verified
  disassembly mapping are decoded: EDC17 → TriCore and Denso/Hitachi →
  SuperH (capstone), and the C166/ST10 families (ME7/ME9/EDC15/EDC16/MS43/
  PPD/SID/Simtec/EMS2000) via the Rust decoder (``_rs/src/arch/c166.rs``);
  anything else is skipped with a recorded reason.
- **Statically resolvable references only.**  No register-state tracking.
  v1 collects: TriCore ``movh.a aN, #hi`` immediately followed by
  ``lea aN, [aN]disp`` (the compiler's canonical absolute-address
  materialisation — measured on the real 4 MB EDC17 corpus: 121
  references landing in 10 high-score tables), and SuperH absolute
  ``mov.l/mov.w ADDR, rN`` operands.  ``addih.a`` is segment-relative and
  deliberately excluded.  a0-resolution (item-4 cheap win, 2026-08-27)
  additionally recovers the boot-time global base ``a0`` (``movh.a a0,#hi``
  + ``lea a0,[a0]disp``) and resolves the ``lea aN, [a0]disp`` accesses
  against it — buffered during the same decode pass, resolved afterwards,
  presence-only (no ``a0`` init → nothing extra).
- **Address translation.**  ECU code references physical addresses (EDC17
  calibrations live at 0x80000000+) while the file is a linear image.
  The load base is auto-detected per file by picking the candidate
  (0, 0x80000000, 0xA0000000, 0xC0000000, 0xD0000000) whose translated
  references most often land inside the caller-supplied data spans
  (minimum hit count required — see ``_MIN_BASE_HITS``).

Domain-neutral: accepts plain ``bytes`` and ``(start, end)`` span tuples —
never ``MapTable`` or layout ``Region`` objects (callers adapt at the
call site, e.g. ``maps.xrefs._table_spans``).
"""

from __future__ import annotations

from typing import NamedTuple

from capstone import (  # type: ignore[import-untyped]
    Cs,
    CS_MODE_BIG_ENDIAN,
    CS_MODE_LITTLE_ENDIAN,
)

from openremap.core.arch import c166
from openremap.core.arch.sh import _extract_sh
from openremap.core.arch.tricore import (
    _extract_tricore,
    _extract_tricore_a0,
    _extract_tricore_pass,
)
from openremap.core.arch.x86 import _extract_x86

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Minimum translated references landing inside the caller-supplied data
#: spans before a non-identity load base is trusted.  Below this the file
#: is treated as identity-mapped (base 0).
_MIN_BASE_HITS = 3

#: Candidate physical load bases tried during address translation.
_BASE_CANDIDATES = (0, 0x80000000, 0xA0000000, 0xC0000000, 0xD0000000)

#: capstone architectures whose mode accepts an endianness flag.
_ENDIAN_MODES = {"sh"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class XrefReport(NamedTuple):
    """Outcome of the code-reference pass over one binary.

    ``referenced`` / ``refs`` hold **file offsets** (physical references
    translated through the detected load base).  ``refs[offset]`` lists the
    disassembled instruction addresses that reference that offset.
    """

    status: str  # "ok" | "skipped"
    skip_reason: str | None  # "unsupported_arch" | "no_code_regions" | ...
    arch: str | None  # "tricore" | "sh" | ...
    endian: str | None  # "little" | "big"
    base_address: int  # detected load base (0 = identity mapping)
    code_bytes_scanned: int
    insn_count: int
    referenced: frozenset[int]
    refs: dict[int, tuple[int, ...]]


def _skip(reason: str) -> XrefReport:
    return XrefReport(
        status="skipped",
        skip_reason=reason,
        arch=None,
        endian=None,
        base_address=0,
        code_bytes_scanned=0,
        insn_count=0,
        referenced=frozenset(),
        refs={},
    )


# ---------------------------------------------------------------------------
# Reference extraction registry
# ---------------------------------------------------------------------------


_EXTRACTORS = {
    "tricore": _extract_tricore,
    "sh": _extract_sh,
    "x86": _extract_x86,
}


# ---------------------------------------------------------------------------
# Core pass
# ---------------------------------------------------------------------------


def _endian_mode(arch_key: str, endian: str) -> int:
    if arch_key in _ENDIAN_MODES:
        if endian == "big":
            return CS_MODE_BIG_ENDIAN
        if endian == "little":
            return CS_MODE_LITTLE_ENDIAN
    return 0


def _detect_base(
    targets: list[int], file_size: int, spans: list[tuple[int, int]]
) -> int:
    """Pick the load base whose translated references hit table data most.

    Requires at least ``_MIN_BASE_HITS`` hits before trusting a non-zero
    base; ties resolve to the smaller base (identity preferred).
    """
    best_base, best_hits = 0, 0
    for b in _BASE_CANDIDATES:
        hits = 0
        for t in set(targets):
            off = t - b
            if 0 <= off < file_size:
                if any(s <= off < e for s, e in spans):
                    hits += 1
        if hits > best_hits:
            best_base, best_hits = b, hits
    if best_hits < _MIN_BASE_HITS:
        return 0
    return best_base


def collect_xrefs(
    data: bytes,
    regions: list[tuple[int, int]],
    arch_info: tuple[str, int, int, bool] | None,
    endian: str | None = None,
    *,
    spans: list[tuple[int, int]] | None = None,
    base_address: int | None = None,
) -> XrefReport:
    """Run the code-reference pass over *data*.

    Args:
        data:        Raw ECU binary content.
        regions:     Code regions as ``(start, end)`` (see
                     :func:`maps.layout.code_regions_from_layout`).
        arch_info:   Output of :func:`arch_for_family`; ``None`` → skipped
                     (``unsupported_arch``).
        endian:      Detected endianness ("little"/"big") — used for arches
                     that accept an endianness mode flag.
        spans:       Optional data spans ``(start, end)`` (e.g. the map
                     tables' data blocks converted by
                     ``maps.xrefs._table_spans``).  When given, the load
                     base is auto-detected from them (see ``_detect_base``);
                     otherwise identity.
        base_address: Optional explicit load base (overrides auto-detect).

    Never raises: any capstone failure yields ``XrefReport("skipped")``
    with a recorded reason.
    """
    if arch_info is None:
        return _skip("unsupported_arch")
    arch_key, arch_const, base_mode, _accepts_endian = arch_info
    if not regions:
        return _skip("no_code_regions")

    if arch_key == "c166":
        # C166/ST10 — decoded in Rust (`_rs/src/arch/c166.rs`), DPP-window
        # translation in `core/arch/c166.py` (no capstone mapping exists).
        return _collect_c166(data, regions, spans or [], base_address)

    mode = base_mode | _endian_mode(arch_key, endian)
    try:
        md = Cs(arch_const, mode)
        md.skipdata = True
    except Exception as exc:  # pragma: no cover - capstone init failure
        return XrefReport(
            status="skipped", skip_reason=f"capstone_init: {exc}",
            arch=arch_key, endian=endian, base_address=0,
            code_bytes_scanned=0, insn_count=0,
            referenced=frozenset(), refs={},
        )

    extract = _EXTRACTORS.get(arch_key)
    if extract is None:  # pragma: no cover - table drift guard
        return _skip(f"no_extractor_for_arch:{arch_key}")

    raw_targets: list[tuple[int, int]] = []
    insn_count = 0
    code_bytes = 0
    # a0-resolution (cheap-wins item 4): buffered ``lea aN, [a0]disp``
    # insns, resolved after the region loop once the boot-time ``a0`` base
    # is known.  Presence-only: no ``a0`` init found → nothing extra.
    a0_base: int | None = None
    a0_lea_insns: list = []

    def _counted(gen: iter) -> iter:
        """Count valid instructions while streaming a decode pass."""
        nonlocal insn_count
        for insn in gen:
            if insn.id != 0:
                insn_count += 1
            yield insn

    try:
        for start, end in regions:
            if end <= start:
                continue
            code_bytes += end - start
            if arch_key == "tricore":
                # Single decode walk: self-contained pairs + boot-time a0
                # init + buffered [a0]-relative leas (never a second pass
                # over the same bytes).
                pairs, a0, leas = _extract_tricore_pass(
                    _counted(md.disasm(data[start:end], start))
                )
                raw_targets.extend(pairs)
                if a0_base is None and a0 is not None:
                    a0_base = a0
                a0_lea_insns.extend(leas)
            else:
                raw_targets.extend(
                    extract(_counted(md.disasm(data[start:end], start)))
                )
    except Exception as exc:  # pragma: no cover - decode guard
        return XrefReport(
            status="skipped", skip_reason=f"decode_error: {exc}",
            arch=arch_key, endian=endian, base_address=0,
            code_bytes_scanned=code_bytes, insn_count=insn_count,
            referenced=frozenset(), refs={},
        )

    if arch_key == "tricore" and a0_base is not None:
        # Resolve the buffered a0-relative leas now that the base is known.
        # The resolved absolute targets join the pool and flow through the
        # existing _detect_base below — no new address translation.
        raw_targets.extend(_extract_tricore_a0(a0_lea_insns, a0_base))

    spans = spans or []
    if base_address is None:
        base_address = _detect_base(
            [t for t, _ in raw_targets], len(data), spans
        )

    referenced: set[int] = set()
    refs: dict[int, list[int]] = {}
    for target, insn_addr in raw_targets:
        off = target - base_address
        if 0 <= off < len(data):
            referenced.add(off)
            refs.setdefault(off, []).append(insn_addr)

    return XrefReport(
        status="ok",
        skip_reason=None,
        arch=arch_key,
        endian=endian,
        base_address=base_address,
        code_bytes_scanned=code_bytes,
        insn_count=insn_count,
        referenced=frozenset(referenced),
        refs={off: tuple(addrs) for off, addrs in refs.items()},
    )


# ---------------------------------------------------------------------------
# C166/ST10 (Rust decoder + DPP-window translation)
# ---------------------------------------------------------------------------


def _collect_c166(
    data: bytes,
    regions: list[tuple[int, int]],
    spans: list[tuple[int, int]],
    base_address: int | None,
) -> XrefReport:
    """C166: Rust-decoded direct-memory operands + address translation.

    Preferred translation reads the boot-time DPP0–3 values
    (``c166.find_dpp_init``) and resolves each reference's exact physical
    address (DPP[page] << 14 | operand & 0x3FFF), detecting the per-file
    flash base with ``c166.detect_dpp_base`` — the C166 analogue of
    ``_detect_base``.  When the DPP init is absent or does not resolve
    cleanly, the pass falls back to the empirical window search
    (``detect_window``: file = (operand & 0x3FFF) + W).  Below the
    minimum hit count the pass reports cleanly with no references
    (presence-only: a missing signal is never a penalty).
    """
    raw, insn_count = c166.collect_references(data, regions)
    code_bytes = sum(e - s for s, e in regions)

    dpp: tuple | None = None
    if base_address is None:
        boot_dpp = c166.find_dpp_init(data)
        if boot_dpp is not None:
            dpp_hit = c166.detect_dpp_base(
                [o for o, _ in raw], boot_dpp, len(data), spans
            )
            if dpp_hit is not None:
                base_address, _dpp_hits = dpp_hit
                dpp = boot_dpp
        if dpp is None:  # no DPP init / unresolved base -> window fallback
            base_address, window_hits = c166.detect_window(
                [o for o, _ in raw], len(data), spans
            )
            if window_hits < c166._MIN_WINDOW_HITS:
                return XrefReport(
                    status="ok", skip_reason=None, arch="c166", endian="little",
                    base_address=0, code_bytes_scanned=code_bytes,
                    insn_count=insn_count, referenced=frozenset(), refs={},
                )
    # explicit base (or the DPP flash base) trusts the caller / detection

    referenced: set[int] = set()
    refs: dict[int, list[int]] = {}
    for off, insn_addr in raw:
        if dpp is not None:
            f = ((dpp[off >> 14] << 14) | (off & 0x3FFF)) - base_address
        else:
            f = (off & 0x3FFF) + base_address
        if 0 <= f < len(data):
            referenced.add(f)
            refs.setdefault(f, []).append(insn_addr)

    return XrefReport(
        status="ok",
        skip_reason=None,
        arch="c166",
        endian="little",
        base_address=base_address,
        code_bytes_scanned=code_bytes,
        insn_count=insn_count,
        referenced=frozenset(referenced),
        refs={off: tuple(addrs) for off, addrs in refs.items()},
    )
