"""
v2 code-reference pipeline — unit tests (synthetic, no corpus).

v2 = v1's decoder calibration (full span set) + a score-gated confirmation
bonus: a table gets +0.06 only when it is BOTH high-confidence
(``score >= 0.85``) AND referenced by code anywhere in its data block.
These tests pin both halves: (1) a low-score table still calibrates
load-base detection (full spans), and (2) the bonus is gated on score, not
on first-cell vs interior byte.  v1's own contract is covered by
``test_xrefs.py`` / ``test_xrefs_corpus.py`` — those must pass unchanged,
which is the proof v1 was not touched.
"""

from __future__ import annotations

import pytest
from capstone import CS_ARCH_X86, CS_MODE_32

from openremap.core.arch.refs import XrefReport
from openremap.core.services.maps.map_hunter import MapTable
from openremap.core.services.maps.xrefs_v2 import adjust_scores_v2, run_xrefs_v2

from tests.conftest import make_layout_bin


def _table(offset, cols=8, rows=4, score=0.8, stride=None) -> MapTable:
    return MapTable(
        offset=offset,
        cols=cols,
        rows=rows,
        cell_width=2,
        byte_order="little",
        x_axis_offset=offset - 16 if offset >= 16 else None,
        y_axis_offset=offset - 8 if offset >= 8 else None,
        score=score,
        stride=stride,
    )


def _xr(referenced=(), status="ok") -> XrefReport:
    return XrefReport(
        status=status,
        skip_reason=None if status == "ok" else "unsupported_arch",
        arch="tricore",
        endian="big",
        base_address=0,
        code_bytes_scanned=1000,
        insn_count=50,
        referenced=frozenset(referenced),
        refs={off: (0x5000,) for off in referenced},
    )


def _x86_arch() -> tuple:
    return ("x86", CS_ARCH_X86, CS_MODE_32, True)


def _mov(addr: int) -> bytes:
    return b"\xA1" + addr.to_bytes(4, "little")


# ---------------------------------------------------------------------------
# adjust_scores_v2 — score-gated bonus
# ---------------------------------------------------------------------------


def test_adjust_scores_v2_gates_on_score():
    # Both referenced; only the high-score table earns the bonus.
    xr = _xr(referenced={0x100, 0x200})
    high = _table(0x100)._replace(score=0.9)
    low = _table(0x200)._replace(score=0.7)
    adjusted = adjust_scores_v2([low, high], xr)
    by_off = {t.offset: t.score for t in adjusted}
    assert by_off[0x100] == 0.96  # high + ref -> +0.06
    assert by_off[0x200] == 0.70  # low + ref -> no bonus


def test_adjust_scores_v2_no_bonus_without_ref():
    xr = _xr(referenced={0x300})
    high = _table(0x100)._replace(score=0.9)
    assert adjust_scores_v2([high], xr)[0].score == 0.9


def test_adjust_scores_v2_interior_ref_counts_for_high_score():
    # A high-score table referenced at an interior byte still earns the
    # bonus (v2.1's first-cell rule wrongly dropped these real maps).
    xr = _xr(referenced={0x102})
    high = _table(0x100)._replace(score=0.9)
    assert adjust_scores_v2([high], xr)[0].score == 0.96


# ---------------------------------------------------------------------------
# run_xrefs_v2 — full-span calibration + score-gated bonus
# ---------------------------------------------------------------------------


def test_run_xrefs_v2_uses_full_spans_for_base_detection():
    # A LOW-score table (below the 0.85 gate) must still calibrate load-base
    # detection — the v2.0→v2.1 regression fix (C166's DPP margin needs the
    # full span set).
    code = _mov(0x80000100) + _mov(0x80000200) + _mov(0x80000300)
    data = code + bytes(0x4000 - len(code))
    low = _table(0x100, cols=64, rows=8, score=0.7)

    xr, tables = run_xrefs_v2(data, _x86_arch(), "little", [(0, len(code))], [low])
    assert xr.status == "ok"
    assert xr.base_address == 0x80000000
    # low score -> no bonus despite the reference
    assert tables[0].score == 0.7


def test_run_xrefs_v2_bonuses_high_score_interior_ref():
    # High-score table referenced at INTERIOR bytes (0x102/0x104/0x106) still
    # gets the bonus — the recall the user asked about is preserved.
    code = _mov(0x80000102) + _mov(0x80000104) + _mov(0x80000106)
    data = code + bytes(0x4000 - len(code))
    table = _table(0x100, cols=64, rows=8, score=0.9)

    xr, tables = run_xrefs_v2(data, _x86_arch(), "little", [(0, len(code))], [table])
    assert xr.status == "ok"
    assert xr.base_address == 0x80000000
    assert tables[0].score == 0.96  # high + interior ref -> bonus


def test_run_xrefs_v2_no_skip_guard():
    # No tables at all -> a known family still decodes (identity base) instead
    # of skipping.
    code = _mov(0x80000100) + _mov(0x80000200) + _mov(0x80000300)
    data = code + bytes(0x4000 - len(code))

    xr, tables = run_xrefs_v2(data, _x86_arch(), "little", [(0, len(code))], [])
    assert xr.status == "ok"
    assert tables == []


# ---------------------------------------------------------------------------
# analyze_binary wiring
# ---------------------------------------------------------------------------


def test_analyze_binary_rejects_unknown_mode():
    from openremap.core.services.analyze import analyze_binary

    with pytest.raises(ValueError):
        analyze_binary(b"\x00" * 4096, "x.bin", xref_mode="bogus")


def test_analyze_binary_v2_no_trusted_guard_on_empty_binary():
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(b"\x00" * 4096, "zero.bin", xref_mode="v2")
    assert report.xrefs is not None
    assert report.xrefs.status == "skipped"
    assert report.xrefs.skip_reason == "no_code_regions"


def test_analyze_binary_v2_runs_on_layout_bin():
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(make_layout_bin(), "layout.bin", xref_mode="v2")
    assert report.xrefs is not None
    assert report.xrefs.skip_reason != "no_trusted_spans"


def test_analyze_binary_default_is_v1():
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(b"\x00" * 4096, "zero.bin")
    assert report.xrefs is not None
    assert report.xrefs.status == "skipped"
    assert report.xrefs.skip_reason != "no_trusted_spans"
