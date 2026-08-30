"""
CPU-architecture domain — family→CPU mapping for the code-reference signal.

The engine principle: a module is named after what it IS, not who uses it.
This package is the domain-neutral home for CPU-architecture
infrastructure shared by map scanning (xrefs), checksum detection (the
C166 decoder, Phase 2) and future dev tooling.  It knows about CPU
architectures and ECU-family name prefixes — never about map tables or
layout regions (it accepts plain bytes and ``(start, end)`` span tuples).

Phase 1 (0.7.3): the family→CPU table and ``arch_for_family``, moved
verbatim from ``services/maps/xrefs.py``.  Phase 2 added the C166/ST10
families, decoded in Rust (``_rs/src/arch/c166.rs``) with the DPP-window
translation in ``core/arch/c166.py``.  Phase 3 adds the CPU-detection
cascade for unknown/unmapped families (extractor-style ``can_handle``,
ordered, first-match-wins, evidence-gated).
"""

from __future__ import annotations

from capstone import (  # type: ignore[import-untyped]
    CS_ARCH_M680X,
    CS_ARCH_M68K,
    CS_ARCH_PPC,
    CS_ARCH_SH,
    CS_ARCH_TRICORE,
    CS_MODE_BIG_ENDIAN,
    CS_MODE_M680X_6800,
    CS_MODE_M680X_6811,
    CS_MODE_M68K_000,
    CS_MODE_SH2,
    CS_MODE_SH2A,
)

# ---------------------------------------------------------------------------
# Architecture table
# ---------------------------------------------------------------------------

#: (arch_key, capstone_arch, base_mode, accepts_endian_flag)
#: Keyed by ECU-family name prefix.  Anything not listed → skipped.
#: Endianness is taken from the extractor's detected ``ecu_endian``, never
#: hardcoded here.  TriCore rejects explicit endianness flags in capstone
#: 5.x (CS_ERR_MODE), so it is not an endian-flag arch.
#:
#: The C166/ST10 families (Phase 2) decode in Rust (`_rs/src/arch/c166.rs`)
#: — there is no capstone mapping, so their tuple is
#: ("c166", 0, 0, False) and the capstone fields are unused.  Registration
#: is by extractor-reported family prefix; MS43 is listed for when its
#: identity extractor lands (the arch lookup is inert until then).
_ARCH_TABLE: dict[str, tuple[str, int, int, bool]] = {
    # capstone-disassembled families (Phase 1 / xrefs v1).
    # EDC16 / MED9 were moved here from the C166 block after the Ghidra
    # oracle verdict (census §4, 2026-08-29): both are TriCore, not C166.
    "EDC17": ("tricore", CS_ARCH_TRICORE, 0, False),
    "MED17": ("tricore", CS_ARCH_TRICORE, 0, False),
    "MED9": ("tricore", CS_ARCH_TRICORE, 0, False),
    "EDC16": ("tricore", CS_ARCH_TRICORE, 0, False),
    "SH7055": ("sh", CS_ARCH_SH, CS_MODE_SH2, True),
    "SH7058": ("sh", CS_ARCH_SH, CS_MODE_SH2, True),
    "SH72531": ("sh", CS_ARCH_SH, CS_MODE_SH2, True),
    "SH72546": ("sh", CS_ARCH_SH, CS_MODE_SH2A, True),
    # C166/ST10 families (Phase 2) — Rust decoder + DPP-window translation.
    # Validated on the real corpus: ME7/ME7.x, ME9, EDC15, MS43, PPD1.x,
    # SID801/803, EMS2000, M5.x, ME1.5.5.
    # Oracle verdicts (census §4, 2026-08-29): EDC16 moved to TriCore and
    # Simtec56 moved to the 8051 block below (Rust MCS-51 decoder), both
    # out of this block.
    "ME7": ("c166", 0, 0, False),
    "ME1.5.5": ("c166", 0, 0, False),
    "ME9": ("c166", 0, 0, False),
    "EDC15": ("c166", 0, 0, False),
    "M5": ("c166", 0, 0, False),
    "MS43": ("c166", 0, 0, False),
    "PPD": ("c166", 0, 0, False),
    "SID": ("c166", 0, 0, False),
    "EMS2000": ("c166", 0, 0, False),
    # MCS-51 / 8051 families (census §6-C) — Rust decoder, no capstone
    # support: M2.x / MP9 / M4.x / SIMOS / Simtec56, plus M1.8 (SAB80C515,
    # census §3).  Identity-mapped (address == file offset).
    "M1.8": ("8051", 0, 0, False),
    "M2": ("8051", 0, 0, False),
    "MP9": ("8051", 0, 0, False),
    "M4": ("8051", 0, 0, False),
    "MONO": ("8051", 0, 0, False),
    "SIMTEC": ("8051", 0, 0, False),
    "SIMOS": ("8051", 0, 0, False),
    # MCS-96 / 8096 families (census §6-C) — Rust decoder.  EDC1 and EDC3
    # are the 8096 (EDC3 confirmed via Ghidra MCS96, 2026-08-29).
    # NOTE: "EDC1" must stay AFTER "EDC15"/"EDC16"/"EDC17" (it is their
    # prefix) so the longer prefixes match first.
    "EDC1": ("mcs96", 0, 0, False),
    "EDC3": ("mcs96", 0, 0, False),
    # M680X families (68HC11 / 6800) — arch census.
    # M1.x is 68HC11 (header 85 0a f0 30); M3.x/MP3/MP7 are the same
    # Motorola line (M3.1 shows accumulator-B ops — flagged, census §4);
    # LH-Jetronic is 6800/6802.
    "M1.3": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "M1.7": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "M1.X": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "M3": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "MP3": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "MP7": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6811, False),
    "LH": ("m680x", CS_ARCH_M680X, CS_MODE_M680X_6800, False),
    # M68K families (68000 / CPU32 / 68332) — census.
    "M1.5": ("m68k", CS_ARCH_M68K, CS_MODE_M68K_000, False),
    "IAW 4LV": ("m68k", CS_ARCH_M68K, CS_MODE_M68K_000, False),
    # PowerPC families — census.
    "MJD": ("ppc", CS_ARCH_PPC, CS_MODE_BIG_ENDIAN, False),
}


def arch_for_family(
    manufacturer: str | None, family: str | None
) -> tuple[str, int, int, bool] | None:
    """Map ``(manufacturer, ecu_family)`` to capstone arch parameters.

    Returns ``(arch_key, capstone_arch, base_mode, accepts_endian_flag)``
    or ``None`` when the family has no verified disassembly mapping.
    """
    if not family:
        return None
    fam = family.strip().upper()
    for prefix, info in _ARCH_TABLE.items():
        if fam.startswith(prefix):
            return info
    return None


# ---------------------------------------------------------------------------
# Decoder labels
# ---------------------------------------------------------------------------

#: Human-friendly decoder names for the xref report / `analyze` decoder tag.
_DECODER_LABELS: dict[str, str] = {
    "c166": "C166 · Rust decoder",
    "8051": "8051 · Rust decoder",
    "mcs96": "MCS-96 · Rust decoder",
    "tricore": "TriCore · capstone",
    "sh": "SuperH · capstone",
    "x86": "x86 · capstone",
    "m680x": "M680X · capstone",
    "m68k": "68K · capstone",
    "ppc": "PowerPC · capstone",
}


def decoder_label(arch_key: str | None) -> str | None:
    """Human-friendly decoder name for an arch key (None when unknown).

    Unknown keys fall back to the raw arch key so the tag never renders
    empty for a future decoder that has not been added to the map yet.
    """
    if not arch_key:
        return None
    return _DECODER_LABELS.get(arch_key, arch_key)
