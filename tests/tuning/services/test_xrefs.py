"""
Code-reference (xref) signal — unit tests + capstone mechanics.

The x86 tests exercise the *mechanics* of the pass (region slicing,
skipdata, absolute-operand extraction, base detection, offset
translation) on hand-encoded x86 — no TriCore/SH encodings needed.
The real-architecture behaviour is covered by the corpus-gated
``tests/tuning/test_xrefs_corpus.py``.

Design contract under test (see ``notes/arch/xrefs.md``):
- presence-only signal — a table is only ever *boosted*, never demoted;
- statically resolvable references only (no register-state tracking);
- base detection is data-driven (candidate load bases), identity default.
"""

from __future__ import annotations

from capstone import CS_ARCH_X86, CS_MODE_32

from openremap.core.arch import arch_for_family
from openremap.core.arch.refs import (
    XrefReport,
    _BASE_CANDIDATES,
    _detect_base,
    collect_xrefs,
)
from openremap.core.arch.tricore import _parse_num
from openremap.core.services.maps.layout import Region, code_regions_from_layout
from openremap.core.services.maps.map_hunter import MapTable
from openremap.core.services.maps.xrefs import (
    _table_spans,
    adjust_table_scores,
    axis_refs_for_table,
    data_refs_for_table,
    xref_evidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table(offset: int, cols: int = 8, rows: int = 4, stride=None) -> MapTable:
    return MapTable(
        offset=offset,
        cols=cols,
        rows=rows,
        cell_width=2,
        byte_order="little",
        x_axis_offset=offset - 16 if offset >= 16 else None,
        y_axis_offset=offset - 8 if offset >= 8 else None,
        score=0.8,
        stride=stride,
    )


def _xr(referenced=(), refs=None, status="ok", base=0, arch="tricore") -> XrefReport:
    return XrefReport(
        status=status,
        skip_reason=None if status == "ok" else "unsupported_arch",
        arch=arch,
        endian="big",
        base_address=base,
        code_bytes_scanned=1000,
        insn_count=50,
        referenced=frozenset(referenced),
        refs=refs or {off: (0x5000,) for off in referenced},
    )


# ---------------------------------------------------------------------------
# arch_for_family
# ---------------------------------------------------------------------------


def test_arch_for_family_edc17_tricore():
    info = arch_for_family("Bosch", "EDC17")
    assert info is not None
    assert info[0] == "tricore"


def test_arch_for_family_denso_and_hitachi_sh():
    for fam in ("SH7055", "SH7058", "SH72531", "SH72546"):
        info = arch_for_family("Denso", fam)
        assert info is not None, fam
        assert info[0] == "sh", fam


def test_arch_for_family_tricore_families():
    # EDC17/MED17 (known) + EDC16/MED9 (Ghidra oracle verdict, census §4) —
    # all TriCore, decoded via capstone.
    for fam in ("EDC16", "EDC17", "MED17", "MED9"):
        info = arch_for_family("Bosch", fam)
        assert info is not None, fam
        assert info[0] == "tricore", fam


def test_arch_for_family_unsupported_is_none():
    # Families with no verified disassembly mapping.  (EDC16/MED9 are TriCore
    # and the 8051/MCS-96 families are served by the Rust decoder.)
    assert arch_for_family("Delphi", "Multec") is None
    assert arch_for_family("Marelli", "IAW") is None
    assert arch_for_family("Denso", "EE20") is None  # SuperH candidate, no corpus
    assert arch_for_family("SomeOEM", "EDC17") is not None  # family drives


def test_arch_for_family_missing_family_is_none():
    assert arch_for_family(None, None) is None
    assert arch_for_family("Bosch", None) is None


def test_arch_for_family_case_insensitive():
    assert arch_for_family("bosch", "edc17") == arch_for_family("Bosch", "EDC17")


def test_arch_for_family_m680x_families():
    # arch census (notes/arch/census.md): M1.x/M3.x/MP3/MP7 are 68HC11,
    # LH-Jetronic is 6800/6802.
    for fam in ("M1.3", "M1.7", "M1.x", "M3.1", "M3.3", "MP3.2", "MP7.2", "LH-Jetronic"):
        info = arch_for_family("Bosch", fam)
        assert info is not None, fam
        assert info[0] == "m680x", fam


def test_arch_for_family_m68k_and_ppc():
    assert arch_for_family("Bosch", "M1.5.5")[0] == "m68k"
    assert arch_for_family("Bosch", "M1.55")[0] == "m68k"
    assert arch_for_family("Marelli", "IAW 4LV")[0] == "m68k"
    assert arch_for_family("Marelli", "MJD 6JF")[0] == "ppc"


def test_arch_for_family_8051_mapped():
    # 8051 families are served by the Rust decoder (census §6-C) — not None.
    for fam in ("M1.8", "M2.9", "MP9", "M4.3", "Mono-Motronic"):
        info = arch_for_family("Bosch", fam)
        assert info is not None and info[0] == "8051", fam
    for fam in ("SIMOS", "Simtec56"):
        info = arch_for_family("Siemens", fam)
        assert info is not None and info[0] == "8051", fam


def test_arch_for_family_mcs96_mapped():
    # EDC1 (8096) is served by the Rust decoder (census §6-C) — not None.
    info = arch_for_family("Bosch", "EDC1")
    assert info is not None and info[0] == "mcs96"


def test_arch_for_family_unknown_unmapped():
    # EE20 (SuperH candidate, no corpus) and unknown families stay unmapped.
    assert arch_for_family("Denso", "EE20") is None
    assert arch_for_family("Bosch", "Multec") is None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_parse_num_forms():
    assert _parse_num("0x1234") == 0x1234
    assert _parse_num("#8") == 8
    assert _parse_num("-0x4f6c") == -0x4F6C
    assert _parse_num("-8") == -8


def test_code_regions_from_layout_filters():
    regions = [
        Region(0, 100, "code", None, 0.7, 7.0, 0, 0, 0.7),
        Region(100, 200, "calibration", None, 0.8, 5.0, 5, 2, 0.8),
        Region(200, 300, "code", None, 0.7, 7.0, 0, 0, 0.7),
        Region(300, 400, "erased", 0xFF, 0.99, 0.0, 0, 0, 0.95),
    ]
    assert code_regions_from_layout(regions) == [(0, 100), (200, 300)]


def test_detect_base_remapped_wins():
    targets = [0x80000100, 0x80000200, 0x80000300]
    assert _detect_base(targets, file_size=0x4000, spans=[(0x100, 0x400)]) == 0x80000000


def test_detect_base_identity_default():
    targets = [0x100, 0x200, 0x300]
    assert _detect_base(targets, file_size=0x4000, spans=[(0x100, 0x400)]) == 0


def test_detect_base_below_threshold_falls_back_to_identity():
    # Only 2 of 3 targets translate+hit — below _MIN_BASE_HITS (3).
    targets = [0x80000100, 0x80000200, 0x12345678]
    assert _detect_base(targets, file_size=0x4000, spans=[(0x100, 0x400)]) == 0


# ---------------------------------------------------------------------------
# Reference matching
# ---------------------------------------------------------------------------


def test_data_refs_contiguous():
    xr = _xr(referenced={0x100, 0x103, 0x200})  # 0x100..0x110 = 8 cells × 2 B
    assert data_refs_for_table(_table(0x100, cols=8, rows=1), xr) == [0x100, 0x103]


def test_data_refs_compound_stride():
    # strided half: rows at offset + r*stride (stride 32 = 16 cols × 2 B)
    xr = _xr(referenced={0x100, 0x120, 0x200})
    table = _table(0x100, cols=8, rows=2, stride=32)
    assert data_refs_for_table(table, xr) == [0x100, 0x120]


def test_axis_refs_are_evidence_only():
    xr = _xr(referenced={0xF0, 0x100})  # x_axis at 0xF0..0x100 (8×2), data at 0x100
    table = _table(0x100, cols=8, rows=1)
    assert axis_refs_for_table(table, xr) == [0xF0]
    assert data_refs_for_table(table, xr) == [0x100]


def test_xref_evidence_shape_and_skipped_empty():
    xr = _xr(referenced={0x100}, refs={0x100: (0x5000, 0x5002)})
    ev = xref_evidence(_table(0x100, cols=8, rows=1), xr)
    assert ev["referenced_by_code"] is True
    assert ev["data_refs"] == [0x100]
    assert ev["insns"] == [0x5000, 0x5002]

    skipped = _xr(referenced={0x100}, status="skipped")
    assert xref_evidence(_table(0x100), skipped) == {}


# ---------------------------------------------------------------------------
# adjust_table_scores
# ---------------------------------------------------------------------------


def test_adjust_bonus_applied_capped_and_resorted():
    xr = _xr(referenced={0x100})
    ref_table = _table(0x100, cols=8, rows=1)  # data 0x100..0x110
    ref_table = ref_table._replace(score=0.95)
    other = _table(0x200, cols=8, rows=1)._replace(score=0.93)
    adjusted = adjust_table_scores([other, ref_table], xr)
    by_offset = {t.offset: t for t in adjusted}
    assert by_offset[0x100].score == 1.0  # 0.95 + 0.06 capped
    assert by_offset[0x200].score == 0.93  # untouched
    # referenced table must sort above the untouched one
    assert adjusted[0].offset == 0x100


def test_adjust_noop_when_skipped():
    xr = _xr(referenced={0x100}, status="skipped")
    tables = [_table(0x100, cols=8, rows=1), _table(0x200, cols=8, rows=1)]
    adjusted = adjust_table_scores(tables, xr)
    assert [t.score for t in adjusted] == [0.8, 0.8]


# ---------------------------------------------------------------------------
# collect_xrefs — capstone mechanics on x86 (hand-encoded)
# ---------------------------------------------------------------------------


def _x86_arch() -> tuple:
    return ("x86", CS_ARCH_X86, CS_MODE_32, True)


def test_collect_xrefs_x86_absolute_and_relative():
    # mov eax, [0x1234]  (A1 imm32)
    # mov eax, [ebx]     (8B 03) — base-register, must NOT be a reference
    # mov eax, [0x100]   (A1 00 01 00 00)
    # nop                (90)
    code = b"\xA1\x34\x12\x00\x00" + b"\x8B\x03" + b"\xA1\x00\x01\x00\x00" + b"\x90"
    data = code + bytes(0x2000 - len(code))
    xr = collect_xrefs(data, [(0, len(code))], _x86_arch(), "little")
    assert xr.status == "ok"
    assert xr.referenced == {0x1234, 0x100}
    assert 0x100 in xr.refs


def test_collect_xrefs_x86_out_of_range_discarded():
    # mov eax, [0xFFFFFF] — target beyond the 4 KB buffer → discarded
    code = b"\xA1\xFF\xFF\x0F\x00"
    data = code + bytes(4096 - len(code))
    xr = collect_xrefs(data, [(0, len(code))], _x86_arch(), "little")
    assert xr.status == "ok"
    assert xr.referenced == frozenset()


def test_collect_xrefs_x86_base_detection_from_tables():
    # Three absolute refs to 0x80000xxx — the load base 0x80000000 must be
    # inferred because a table's data span covers the translated offsets.
    code = (
        b"\xA1\x00\x01\x00\x80"  # mov eax, [0x80000100]
        + b"\xA1\x00\x02\x00\x80"  # mov eax, [0x80000200]
        + b"\xA1\x00\x03\x00\x80"  # mov eax, [0x80000300]
    )
    data = code + bytes(0x4000 - len(code))
    table = _table(0x100, cols=64, rows=8)._replace(score=0.9)
    xr = collect_xrefs(
        data, [(0, len(code))], _x86_arch(), "little", spans=_table_spans([table])
    )
    assert xr.status == "ok"
    assert xr.base_address == 0x80000000
    assert xr.referenced == {0x100, 0x200, 0x300}
    # and the table now carries a bonus
    adjusted = adjust_table_scores([table], xr)
    assert adjusted[0].score == 0.96


def test_collect_xrefs_skips_when_arch_unsupported():
    data = bytes(1024)
    xr = collect_xrefs(data, [(0, 512)], None, "little")
    assert xr.status == "skipped"
    assert xr.skip_reason == "unsupported_arch"


def test_collect_xrefs_skips_when_no_code_regions():
    data = bytes(1024)
    xr = collect_xrefs(data, [], _x86_arch(), "little")
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_code_regions"


def test_collect_xrefs_never_raises_on_bad_arch():
    from capstone import CS_ARCH_ALL

    data = bytes(1024)
    xr = collect_xrefs(data, [(0, 512)], ("x86", CS_ARCH_ALL, 0, False), "little")
    assert xr.status == "skipped"
    assert xr.skip_reason.startswith("capstone_init")


def test_base_candidates_ordered_identity_first():
    # Deterministic candidate order matters for ties — identity preferred.
    assert _BASE_CANDIDATES[0] == 0
