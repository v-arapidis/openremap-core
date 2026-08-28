"""
Code-reference (xref) signal — corpus-gated tests.

Skips cleanly when ``tests/data/`` is absent (CI never has it), matching
the pattern of every other corpus test.  The assertions are intentionally
soft (presence/absence of a working pass, never hard counts or specific
addresses) because the corpus content varies per checkout.

Measured reality (2026-08-26, real 4 MB EDC17 + Subaru SH-2):
- EDC17: TriCore, load base 0x80000000, hundreds of statically-resolvable
  ``movh.a``+``lea`` pairs → tables in the genuine calibration area get
  the bonus.
- Subaru SH-2: code uses register-indirect addressing almost exclusively
  → the absolute-reference signal legitimately stays empty.  The pass
  must still run cleanly (status ``ok``) — absence of references is not
  an error (presence-only design).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import arch_for_family
from openremap.core.arch.refs import collect_xrefs
from openremap.core.services.convert import decode_image
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.layout import code_regions_from_layout, segment
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.maps.xrefs import (
    _table_spans,
    adjust_table_scores,
    data_refs_for_table,
)

_DATA = Path("tests/data")
_EDC17 = _DATA / "ECUs/Bosch/EDC17/1__1__1.bin"


def _has_corpus() -> bool:
    return _EDC17.exists()


def test_edc17_xref_signal_fires():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    data = _EDC17.read_bytes()
    ident = identify_ecu(data, _EDC17.name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "tricore"

    tables = scan_map_tables(data, min_score=0.55)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    assert codes, "expected code regions on the real EDC17"

    xr = collect_xrefs(
        data, codes, arch, ident.get("ecu_endian"), spans=_table_spans(tables)
    )
    assert xr.status == "ok"
    assert xr.base_address == 0x80000000
    assert len(xr.referenced) > 0

    adjusted = adjust_table_scores(tables, xr)
    bonused = [t for t in adjusted if data_refs_for_table(t, xr)]
    assert len(bonused) > 0
    # scores only ever go up (presence-only contract)
    orig = {t.offset: t.score for t in tables}
    assert all(t.score >= orig[t.offset] for t in adjusted)


def test_subaru_sh_xref_runs_cleanly():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    hexes = sorted((_DATA / "ECUs/Subaru").rglob("*.hex"))
    if not hexes:
        pytest.skip("no Subaru corpus files present")
    data = decode_image(hexes[0].read_bytes()).data
    ident = identify_ecu(data, hexes[0].name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "sh"

    tables = scan_map_tables(data, min_score=0.55)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    assert codes, "expected code regions on the Subaru SH-2 ROM"

    xr = collect_xrefs(
        data, codes, arch, ident.get("ecu_endian"), spans=_table_spans(tables)
    )
    assert xr.status == "ok"
    # SH code is register-indirect — no absolute refs is legitimate.
    assert len(xr.referenced) >= 0
    assert xr.base_address == 0  # identity-mapped ROM


def test_edc17_xrefs_never_raise_via_analyze_service():
    """analyze_binary must tolerate the xref pass end-to-end on a real bin."""
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    from openremap.core.services.analyze import analyze_binary

    data = _EDC17.read_bytes()
    report = analyze_binary(
        data, _EDC17.name, skip_maps=False, fast=False,
        container="raw binary",
    )
    assert report.xrefs is not None
    d = report.to_dict()
    assert d["xrefs"]["status"] == "ok"
    assert isinstance(d["xrefs"]["reference_count"], int)
    # every top table carries an xref evidence dict
    for t in d["maps"]["tables"]:
        assert "xref" in t
        assert set(t["xref"].keys()) <= {
            "referenced_by_code", "data_refs", "axis_refs", "insns",
        }


def test_edc17_a0_signal_resolves():
    """a0-resolution fires on the real EDC17 (cheap-wins item 4).

    The boot-time ``a0`` init is found in the 0xd0000000 window and the
    thousands of ``lea aN, [a0]disp`` accesses resolve to in-file offsets
    under it.  Soft structural assertions only (presence / magnitude, no
    hard counts): the a0 signal is presence-only by design — the default
    flash-base run keeps its existing behaviour, and translating under the
    a0-window base (a ``_BASE_CANDIDATES`` entry) surfaces the a0-relative
    references in bulk.
    """
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    from capstone import CS_ARCH_TRICORE, Cs

    from openremap.core.arch.tricore import (
        _extract_tricore_a0,
        _extract_tricore_pass,
    )

    data = _EDC17.read_bytes()
    ident = identify_ecu(data, _EDC17.name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "tricore"

    tables = scan_map_tables(data, min_score=0.55)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    assert codes

    md = Cs(CS_ARCH_TRICORE, 0)
    md.skipdata = True
    a0_base = None
    all_leas: list = []
    for start, end in codes:
        _, a0, leas = _extract_tricore_pass(md.disasm(data[start:end], start))
        all_leas.extend(leas)
        if a0_base is None and a0 is not None:
            a0_base = a0

    assert a0_base is not None, "expected a boot-time a0 init on real EDC17"
    assert a0_base >> 16 == 0xD000, f"a0 base {a0_base:#x} outside the 0xd0000000 window"
    assert len(all_leas) > 1000, "expected thousands of [a0]-relative leas"

    resolved = _extract_tricore_a0(all_leas, a0_base)
    assert len(resolved) == len(all_leas)
    in_file = sum(
        1 for t, _ in resolved if 0 <= t - 0xD0000000 < len(data)
    )
    assert in_file > 1000, (
        "a0-relative targets should map into the file under the "
        "0xd0000000 window"
    )

    # The resolved a0 signal is an order of magnitude larger than the
    # self-contained-pair signal: under the a0-window base, the reference
    # count is substantially higher than under the default flash base
    # (soft growth assertion — the exact ratio varies per checkout).
    spans = _table_spans(tables)
    xr_flash = collect_xrefs(
        data, codes, arch, ident.get("ecu_endian"), spans=spans
    )
    xr_a0win = collect_xrefs(
        data, codes, arch, ident.get("ecu_endian"), spans=spans,
        base_address=0xD0000000,
    )
    assert xr_flash.status == "ok" and xr_a0win.status == "ok"
    assert xr_flash.base_address == 0x80000000  # default base unchanged
    assert len(xr_a0win.referenced) > 5 * len(xr_flash.referenced)
