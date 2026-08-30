"""
C166/ST10 xref path — unit tests (Rust decoder + DPP-window translation).

The Rust decoder (`openremap._rust.c166_references`, `_rs/src/arch/c166.rs`)
is covered by its own `cargo test` suite; these tests pin the Python
adapter (`core/arch/c166.py`) and the `collect_xrefs` c166 branch
(`core/arch/refs.py`) on synthetic code bytes and spans.
"""

from __future__ import annotations

from openremap.core.arch import arch_for_family, c166
from openremap.core.arch.refs import collect_xrefs

#: The c166 arch tuple as returned by `arch_for_family` for C166 families.
_C166 = ("c166", 0, 0, False)


# ---------------------------------------------------------------------------
# Rust decoder adapter
# ---------------------------------------------------------------------------


def test_c166_references_direct_mem_forms():
    # MOV R0, 0x1234 (F2 00 34 12) + MOVB mem, R1 (F7 01 78 56) + MOV R2, #8
    # (E0 20 — immediate, not a ref) + MOV mem, R3 (F6 03 00 20).
    code = bytes([0xF2, 0x00, 0x34, 0x12, 0xF7, 0x01, 0x78, 0x56,
                  0xE0, 0x20, 0xF6, 0x03, 0x00, 0x20])
    refs, insn_count = c166.collect_references(code, [(0, len(code))])
    assert refs == [(0x1234, 0), (0x5678, 4), (0x2000, 10)]
    assert insn_count == 4


def test_c166_references_respect_region_bounds():
    code = bytes([0xF2, 0x00, 0x34, 0x12])
    refs, _ = c166.collect_references(code, [(0, 2)])  # truncated region
    assert refs == []
    refs, _ = c166.collect_references(code, [])
    assert refs == []


# ---------------------------------------------------------------------------
# DPP-window detection
# ---------------------------------------------------------------------------


def test_detect_window_finds_best_base():
    # 10 offsets that translate into the span [0x12000, 0x12010) only at
    # window W=0x10000 (file = (o & 0x3FFF) + W).
    offsets = [0x2000 + i for i in range(10)]
    spans = [(0x12000, 0x12010)]
    w, hits = c166.detect_window(offsets, file_size=0x20000, spans=spans)
    assert w == 0x10000
    assert hits == 10


def test_detect_window_below_threshold_reports_no_signal():
    # 2 hits < _MIN_WINDOW_HITS (8) → no window trusted.
    offsets = [0x2000, 0x2001]
    spans = [(0x12000, 0x12010)]
    w, hits = c166.detect_window(offsets, file_size=0x20000, spans=spans)
    assert w == 0
    assert hits == 2


def test_detect_window_identity_window():
    # Offsets that hit spans without any shift (W=0).
    offsets = [0x0001 + i for i in range(10)]
    spans = [(0x0000, 0x0020)]
    w, hits = c166.detect_window(offsets, file_size=0x20000, spans=spans)
    assert w == 0
    assert hits == 10


# ---------------------------------------------------------------------------
# collect_xrefs c166 branch
# ---------------------------------------------------------------------------


def _c166_bin(n_refs: int = 10, file_size: int = 0x20000) -> bytes:
    """Code bytes (n_refs direct-mem refs to 0x2000..) + padding to
    *file_size* — the table span at 0x12000 sits inside the file."""
    code = bytearray()
    for i in range(n_refs):
        code += bytes([0xF2, 0x00, 0x00 + i, 0x20])  # MOV R0, 0x2000+i
    return bytes(code) + bytes(file_size - len(code))


def test_collect_xrefs_c166_ok_with_window():
    data = _c166_bin()
    spans = [(0x12000, 0x12010)]  # table data at file 0x12000
    xr = collect_xrefs(data, [(0, 40)], _C166, "little", spans=spans)
    assert xr.status == "ok"
    assert xr.arch == "c166"
    assert xr.base_address == 0x10000
    assert xr.referenced == frozenset(range(0x12000, 0x1200A))
    # the referencing instruction of the first ref sits at file offset 0
    assert xr.refs[0x12000][0] == 0
    assert xr.refs[0x12006][0] == 24


def test_collect_xrefs_c166_skips_without_code_regions():
    xr = collect_xrefs(b"\x00" * 16, [], _C166, "little")
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_code_regions"


def test_collect_xrefs_c166_below_threshold_reports_clean():
    data = _c166_bin(n_refs=2)
    spans = [(0x12000, 0x12010)]
    xr = collect_xrefs(data, [(0, 8)], _C166, "little", spans=spans)
    assert xr.status == "ok"
    assert xr.base_address == 0
    assert xr.referenced == frozenset()  # no signal → no references


def test_collect_xrefs_c166_explicit_base_trusted():
    data = _c166_bin()
    spans = [(0x12000, 0x12010)]
    xr = collect_xrefs(
        data, [(0, 40)], _C166, "little", spans=spans, base_address=0x10000
    )
    assert xr.status == "ok"
    assert xr.base_address == 0x10000
    assert xr.referenced == frozenset(range(0x12000, 0x1200A))


# ---------------------------------------------------------------------------
# Boot DPP init parsing + DPP-value address resolution
# ---------------------------------------------------------------------------

#: ``MOV DPP0..3, #pag`` = ``E6 <sfr-index> <lo> <hi>`` (LE page value).
_BOOT_DPP_INIT = bytes([
    0xE6, 0x00, 0x04, 0x02,  # MOV DPP0, #0x204
    0xE6, 0x01, 0x05, 0x02,  # MOV DPP1, #0x205
    0xE6, 0x02, 0xE0, 0x00,  # MOV DPP2, #0xE0
    0xE6, 0x03, 0x03, 0x00,  # MOV DPP3, #0x3
])


def test_find_dpp_init_parses_boot_quadruple():
    assert c166.find_dpp_init(_BOOT_DPP_INIT) == (0x204, 0x205, 0xE0, 0x3)


def test_find_dpp_init_first_write_wins_and_skips_non_dpp():
    # DPP0 written twice (ISR-style prologue then re-init): first value wins.
    boot = bytes([
        0xE6, 0x00, 0x04, 0x02,  # first DPP0 write -> 0x204 kept
        0xE6, 0x0F, 0xAA, 0xBB,  # MOV R15, #0xBBAA — not a DPP write
        0xE6, 0x00, 0x02, 0x02,  # later DPP0 write -> ignored
        0xE6, 0x01, 0x05, 0x02,
        0xE6, 0x02, 0xE0, 0x00,
        0xE6, 0x03, 0x03, 0x00,
    ])
    assert c166.find_dpp_init(boot) == (0x204, 0x205, 0xE0, 0x3)


def test_find_dpp_init_absent_or_incomplete():
    assert c166.find_dpp_init(b"") is None
    assert c166.find_dpp_init(b"\x00" * 64) is None
    # only three of four DPPs written
    partial = bytes([
        0xE6, 0x00, 0x04, 0x02, 0xE6, 0x01, 0x05, 0x02, 0xE6, 0x02, 0xE0, 0x00,
    ])
    assert c166.find_dpp_init(partial) is None
    # a page value >= 0x4000 is not a 14-bit DPP page -> init rejected
    bad = bytes([
        0xE6, 0x00, 0x00, 0x40,  # DPP0 = 0x4000 — out of range
        0xE6, 0x01, 0x05, 0x02, 0xE6, 0x02, 0xE0, 0x00, 0xE6, 0x03, 0x03, 0x00,
    ])
    assert c166.find_dpp_init(bad) is None


def test_detect_dpp_base_prefers_exact_flash_base():
    dpp = (0x204, 0x205, 0xE0, 0x3)
    # page-1 operands 0x4000+i: phys = 0x205<<14 | i = 0x814000+i, which
    # lands in file 0x14000+i only at flash base 0x800000.
    offsets = [0x4000 + i for i in range(10)]
    spans = [(0x14000, 0x14010)]
    base, hits = c166.detect_dpp_base(offsets, dpp, file_size=0x200000, spans=spans)
    assert base == 0x800000
    assert hits == 10


def test_detect_dpp_base_rejects_tied_bases():
    # Two candidate bases map the same refs into spans equally -> ambiguous.
    dpp = (0x40, 0x50, 0x60, 0x70)  # phys windows 0x100000 / 0x140000 / ...
    offsets = [0x0000 + i for i in range(8)]  # page 0 -> phys 0x100000+i
    spans = [(0x100000, 0x100008), (0x000000, 0x000008)]  # base 0 and 0x100000 tie
    assert c166.detect_dpp_base(offsets, dpp, file_size=0x200000, spans=spans) is None


def test_detect_dpp_base_below_threshold_returns_none():
    dpp = (0x204, 0x205, 0xE0, 0x3)
    offsets = [0x4000 + i for i in range(3)]  # 3 hits < _MIN_WINDOW_HITS (8)
    spans = [(0x14000, 0x14010)]
    assert c166.detect_dpp_base(offsets, dpp, file_size=0x200000, spans=spans) is None


def _c166_dpp_bin(refs: list[int], file_size: int = 0x20000) -> bytes:
    """Boot DPP init + one MOV R0, <operand> per entry in *refs*."""
    code = bytearray()
    for o in refs:
        code += bytes([0xF2, 0x00, o & 0xFF, (o >> 8) & 0xFF])  # MOV R0, mem
    return _BOOT_DPP_INIT + bytes(code) + bytes(file_size - len(_BOOT_DPP_INIT) - len(code))


def test_collect_xrefs_c166_uses_dpp_init():
    # page-1 operands (0x4000+i): phys 0x814000+i -> file 0x14000+i at
    # the exact flash base 0x800000 (the window search never finds this).
    refs = [0x4000 + i for i in range(10)]
    refs.append(0x8000)  # page-2 operand: DPP2=0xE0 -> phys 0x380000, out of file
    data = _c166_dpp_bin(refs)
    spans = [(0x14000, 0x14010)]
    xr = collect_xrefs(data, [(16, 16 + 11 * 4)], _C166, "little", spans=spans)
    assert xr.status == "ok"
    assert xr.base_address == 0x800000
    assert xr.referenced == frozenset(range(0x14000, 0x1400A))
    assert 0x8000 not in xr.referenced  # page-2 ref resolves outside the file
    assert xr.refs[0x14000][0] == 16


def test_collect_xrefs_c166_dpp_falls_back_to_window():
    # Boot DPP init exists, but the refs are page-2 operands whose DPP2
    # window (0xE0 << 14 = 0x380000, RAM) resolves outside the file for
    # every candidate flash base -> the empirical window search takes
    # over unchanged (16 KB window W=0x10000).
    refs = [0xA000 + i for i in range(10)]  # page 2, low bits 0x2000..0x2009
    data = _c166_dpp_bin(refs)
    spans = [(0x12000, 0x12010)]  # reachable via window W=0x10000
    xr = collect_xrefs(data, [(16, 16 + 10 * 4)], _C166, "little", spans=spans)
    assert xr.status == "ok"
    assert xr.base_address == 0x10000
    assert xr.referenced == frozenset(range(0x12000, 0x1200A))


# ---------------------------------------------------------------------------
# Family registration
# ---------------------------------------------------------------------------


def test_arch_for_family_c166_families():
    for family in ("ME7", "ME7.1.1", "ME9", "EDC15", "MS43",
                   "PPD1.1", "SID803", "EMS2000"):
        info = arch_for_family("Bosch" if family.startswith(("ME", "EDC")) else "Siemens", family)
        assert info is not None, family
        assert info[0] == "c166", family


def test_arch_for_family_c166_prefixes_do_not_collide():
    # MEDC17 / ME17 (TriCore-era) must NOT match the ME7/ME9 C166 prefixes.
    assert arch_for_family("Bosch", "MEDC17") is None
    assert arch_for_family("Bosch", "ME17") is None
