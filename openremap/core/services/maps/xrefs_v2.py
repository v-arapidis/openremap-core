"""
Code-reference (xref) pipeline — v2, the score-gated confirmation variant.

A parallel, opt-in alternative to the default v1 logic in
``services/analyze.py``.  v2 keeps v1's decoder calibration EXACTLY as-is
(full span set for load-base detection and the arch cascade) and changes
only the *confirmation* signal: the +0.06 bonus is applied to a table only
when it is BOTH already high-confidence (structural ``score >= 0.85`` —
the corpus-validated "calibration, never code" floor) AND referenced by
code anywhere inside its data block.

Why not "first cell only" (v2.1): measured on real bins, a first-cell
reference is a poor proxy for map quality — it dropped 21 of the 36
high-score referenced tables (real maps the code reaches at interior
cells) while keeping 141 low-score ones.  The structural score is the real
quality signal; gating the bonus on it confirms the right tables and
leaves the junk alone.

Unwire: delete this module and the ``xref_mode == "v2"`` branch in
``analyze.py``, or flip the default back to ``"v1"``.
"""

from __future__ import annotations

from openremap.core.arch.detect import detect_arch
from openremap.core.arch.refs import XrefReport, collect_xrefs
from openremap.core.services.maps.map_hunter import MapTable
from openremap.core.services.maps.xrefs import _table_spans, data_refs_for_table

#: Structural-score floor for the confirmation bonus.  Matches ``layout.py``'s
#: corpus-validated threshold: tables scoring >= 0.85 appear only in
#: calibration sectors, never in code.
_TRUSTED_SCORE = 0.85

#: Additive bonus, same magnitude as v1 (``xrefs._XREF_DATA_BONUS``).
_V2_DATA_BONUS = 0.06


def adjust_scores_v2(tables, xref: XrefReport) -> list[MapTable]:
    """Score-gated bonus: high-confidence table + any code reference.

    Re-sort mirrors v1: score desc, area desc, 2D-over-1D, offset asc.
    """
    if xref.status != "ok":
        return tables

    def _bonused(t: MapTable) -> MapTable:
        if t.score >= _TRUSTED_SCORE and data_refs_for_table(t, xref):
            return t._replace(score=min(1.0, round(t.score + _V2_DATA_BONUS, 3)))
        return t

    adjusted = [_bonused(t) for t in tables]
    return sorted(
        adjusted,
        key=lambda t: (
            -t.score,
            -(t.cols * t.rows),
            0 if t.rows > 1 else 1,
            t.offset,
        ),
    )


def run_xrefs_v2(
    data: bytes,
    arch_info,
    endian,
    codes,
    tables: list[MapTable],
) -> tuple[XrefReport, list[MapTable]]:
    """Run the v2 pipeline; return ``(xrefs, tables)``.

    Decoder calibration (load-base detection + arch cascade) uses the FULL
    table set — byte-identical to v1.  Only the confirmation bonus is
    score-gated.  No skip guard: a known family still decodes and reports
    identity-mapped references even when no table scores highly.
    """
    spans = _table_spans(tables)
    if arch_info is None:
        xrefs = detect_arch(data, codes, endian, spans)
    else:
        xrefs = collect_xrefs(data, codes, arch_info, endian, spans=spans)

    if xrefs.status == "ok":
        tables = adjust_scores_v2(tables, xrefs)
    return xrefs, tables
