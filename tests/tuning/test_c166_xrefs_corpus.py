"""
C166 xref signal — corpus-gated tests (ME7 / EDC15 / MS43).

Skips cleanly when ``tests/data/`` is absent (CI never has it), matching the
pattern of every other corpus test.  The C166 decoder is Rust
(``_rs/src/arch/c166.rs``); the DPP-window translation lives in
``core/arch/c166.py``.  Assertions are soft (presence of a working pass,
never hard counts) because corpus content varies per checkout.

Measured reality (2026-08-26, real corpus):
- ME7 / EDC15: thousands of direct-memory operands translate into table
  data spans at a single DPP window base → high-score tables get the bonus.
- MS43: identified by the Siemens MS43 extractor
  (``openremap/core/manufacturers/siemens/ms43/``), which drives the C166
  arch tuple through ``arch_for_family("Siemens", "MS43")``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import arch_for_family
from openremap.core.arch.refs import collect_xrefs
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.layout import code_regions_from_layout, segment
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.maps.xrefs import (
    _table_spans,
    adjust_table_scores,
    data_refs_for_table,
)

_DATA = Path("tests/data")


def _has_corpus() -> bool:
    return (_DATA / "ECUs/Bosch/ME7").exists()


def _first_bin(rel: str) -> Path:
    d = _DATA / rel
    bins = sorted(d.glob("*.ori")) + sorted(d.glob("*.bin")) + sorted(d.glob("*.hex"))
    return bins[0] if bins else None


def _run_c166(data: bytes, arch) -> tuple:
    """Full pipeline: scan → segment → collect_xrefs → adjust scores."""
    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    xr = collect_xrefs(data, codes, arch, "little", spans=_table_spans(tables))
    adjusted = adjust_table_scores(tables, xr)
    bonused = [t for t in adjusted if data_refs_for_table(t, xr)]
    return xr, tables, bonused


def test_me7_c166_xref_signal_fires():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    path = _first_bin("ECUs/Bosch/ME7")
    if path is None:
        pytest.skip("no ME7 corpus files present")
    data = path.read_bytes()
    ident = identify_ecu(data, path.name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "c166"

    xr, tables, bonused = _run_c166(data, arch)
    assert xr.status == "ok"
    assert xr.base_address > 0, "expected a DPP window base on real ME7"
    assert len(xr.referenced) > 0
    assert bonused, "expected at least one bonused table on real ME7"
    # presence-only contract: scores only ever go up
    orig = {t.offset: t.score for t in tables}
    for t in bonused:
        assert t.score >= orig[t.offset]


def test_edc15_c166_xref_signal_fires():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    path = _first_bin("ECUs/Bosch/EDC15")
    if path is None:
        pytest.skip("no EDC15 corpus files present")
    data = path.read_bytes()
    ident = identify_ecu(data, path.name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "c166"

    xr, _, bonused = _run_c166(data, arch)
    assert xr.status == "ok"
    assert xr.base_address > 0, "expected a DPP window base on real EDC15"
    assert len(xr.referenced) > 0
    assert bonused, "expected at least one bonused table on real EDC15"


def test_ms43_c166_xref_signal_fires():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    path = _first_bin("ECUs/Siemens/MS43")
    if path is None:
        pytest.skip("no MS43 corpus files present")
    data = path.read_bytes()
    ident = identify_ecu(data, path.name)
    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    assert arch is not None and arch[0] == "c166"

    xr, _, bonused = _run_c166(data, arch)
    assert xr.status == "ok"
    assert xr.base_address > 0, "expected a DPP window base on real MS43"
    assert len(xr.referenced) > 0
    assert bonused, "expected at least one bonused table on real MS43"


def test_c166_analyze_pipeline_never_raises():
    """analyze_binary must tolerate the C166 xref pass end-to-end."""
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    from openremap.core.services.analyze import analyze_binary

    path = _first_bin("ECUs/Bosch/EDC15")
    if path is None:
        pytest.skip("no EDC15 corpus files present")
    data = path.read_bytes()
    report = analyze_binary(data, path.name, skip_maps=False, fast=False)
    assert report.xrefs is not None
    d = report.to_dict()
    assert isinstance(d["xrefs"]["reference_count"], int)
    for t in d["maps"]["tables"]:
        assert "xref" in t
