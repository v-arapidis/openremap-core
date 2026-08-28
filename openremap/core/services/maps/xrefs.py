"""
Code-reference (xref) signal — map-domain score application.

The map-domain consumer of the arch-domain reference collection
(``core/arch/refs.py``).  This module applies the xref evidence to
calibration maps: a table whose **data block** is referenced by code is
almost certainly a genuine calibration map — it receives a small score
bonus and carries evidence ("referenced by code at 0x…").  The
disassembly itself (``collect_xrefs``), the family→CPU table
(``arch_for_family``) and the per-arch extractors live in
``core/arch/`` — this module only knows how to read a table's byte
ranges out of a report, and how to adapt tables to the arch-domain
``(start, end)`` span API.

Design contract (see ``notes/arch/xrefs.md`` — the implementation plan):

- **Presence-only signal, never a penalty.**  Most ECU code reaches maps
  through base-register addressing that cannot be resolved statically
  (e.g. ``lea aN, [a0]disp`` with a runtime global base), so a *missing*
  reference proves nothing.  Absence never demotes a table.
- **Statically resolvable references only.**  No register-state tracking
  (the arch-domain extractors collect only self-contained absolute
  references).
"""

from __future__ import annotations

from openremap.core.arch.refs import XrefReport
from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Additive score bonus for a table whose DATA block is referenced by code.
#: Small and bounded: it nudges ranking without letting a mediocre table
#: jump a threshold.  Validated on the real EDC17 corpus (2026-08-26).
_XREF_DATA_BONUS = 0.06

#: v1: axis references are evidence-only (axes are shared/duplicated).
_XREF_AXIS_BONUS = 0.0


# ---------------------------------------------------------------------------
# Table → span adaptation (feeds the arch-domain load-base detection)
# ---------------------------------------------------------------------------


def _table_spans(tables) -> list[tuple[int, int]]:
    """``(start, end)`` data spans for the given tables.

    Compound (strided) halves: the per-row ranges are their real data
    extent; contiguous tables span their full cell block.
    """
    spans: list[tuple[int, int]] = []
    for t in tables:
        row_bytes = t.cols * t.cell_width
        if t.stride is not None and t.stride != row_bytes:
            for r in range(t.rows):
                s = t.offset + r * t.stride
                spans.append((s, s + row_bytes))
        else:
            spans.append((t.offset, t.offset + t.rows * row_bytes))
    return spans


# ---------------------------------------------------------------------------
# Signal application
# ---------------------------------------------------------------------------


def _row_ranges(table: MapTable) -> list[tuple[int, int]]:
    """Byte range of each data row of *table* (mirrors recipe_maps)."""
    row_bytes = table.cols * table.cell_width
    if table.stride is None or table.stride == row_bytes:
        return [(table.offset, table.offset + table.rows * row_bytes)]
    return [
        (table.offset + r * table.stride, table.offset + r * table.stride + row_bytes)
        for r in range(table.rows)
    ]


def _refs_in_ranges(table: MapTable, xref: XrefReport, ranges) -> list[int]:
    """Referenced offsets (sorted) falling inside the given byte ranges."""
    out = sorted(
        off
        for off in xref.referenced
        if any(s <= off < e for s, e in ranges)
    )
    return out


def data_refs_for_table(table: MapTable, xref: XrefReport) -> list[int]:
    """Referenced offsets inside the table's DATA block (sorted)."""
    return _refs_in_ranges(table, xref, _row_ranges(table))


def axis_refs_for_table(table: MapTable, xref: XrefReport) -> list[int]:
    """Referenced offsets inside the table's axes (evidence only)."""
    ranges: list[tuple[int, int]] = []
    if table.x_axis_offset is not None:
        ranges.append((table.x_axis_offset, table.x_axis_offset + table.cols * 2))
    if table.y_axis_offset is not None:
        ranges.append((table.y_axis_offset, table.y_axis_offset + table.rows * 2))
    return _refs_in_ranges(table, xref, ranges)


def xref_evidence(table: MapTable, xref: XrefReport) -> dict:
    """JSON-safe evidence dict for one table (empty when skipped)."""
    if xref.status != "ok":
        return {}
    data_refs = data_refs_for_table(table, xref)
    axis_refs = axis_refs_for_table(table, xref)
    insns = sorted(
        addr
        for off in data_refs + axis_refs
        for addr in xref.refs.get(off, ())
    )
    return {
        "referenced_by_code": bool(data_refs),
        "data_refs": data_refs,
        "axis_refs": axis_refs,
        "insns": insns[:32],  # bounded evidence
    }


def adjust_table_scores(
    tables: list[MapTable], xref: XrefReport
) -> list[MapTable]:
    """Return *tables* with the xref bonus applied, re-sorted by score.

    No-op (identity scores) when ``xref.status != "ok"``.  Re-sort mirrors
    the Rust scanner's ordering: score desc, area desc, 2D-over-1D,
    offset asc — stable for ties.
    """
    if xref.status != "ok":
        return tables

    def _bonused(t: MapTable) -> MapTable:
        if data_refs_for_table(t, xref):
            return t._replace(score=min(1.0, round(t.score + _XREF_DATA_BONUS, 3)))
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
