"""
Map annotation for recipes — the schema 4.4 ``maps`` layer.

Connects the byte-level diff (``cook``) with the calibration-level view
(``scan-maps`` / ``diff-maps``).  ``attach_maps()`` scans the STOCK binary
for calibration tables and records, for every changed instruction, which
map it lands in.  This is what makes a recipe human-reviewable in a git
workflow: "fuel base map, 3 cells changed" instead of raw hex.

Design rules
------------
- **The maps section is optional and purely informational.**  The patcher
  and validators read only ``instructions`` and ignore ``maps`` by
  construction — a 4.4 recipe patches exactly like a 4.3 one.
- **Lean by design.**  No cell grids are stored — only structural
  descriptors (offset, dims, cell width, endian, axes, stride) plus
  ``instruction_refs`` (indices into ``instructions``).  A typical tune
  (5–50 changed maps) adds a few KB, not hundreds.
- **Probabilistic labels only.**  ``label`` / ``label_confidence`` come
  from :mod:`map_classifier` — never verified map names.
- **Containment honours compound tables.**  For strided halves (two maps
  sharing a Y axis with interleaved rows) each instruction is assigned to
  the half whose actual cell bytes it overlaps.
"""

from __future__ import annotations

import struct

from openremap.core.services.maps.map_classifier import classify_table, family_fuel_type
from openremap.core.services.maps.map_hunter import MapTable, scan_map_tables

MAPS_SCHEMA_VERSION = "4.4"

# Scan parameters — mirror diff-maps: a low score threshold so changed maps
# are not missed, series probing on so shared-axis families are covered.
_SCAN_MIN_SCORE = 0.55
_SCAN_MAX_SERIES = 16

# A tune touches a handful of maps; this guards pathological diffs
# (whole-binary rewrites) from bloating the recipe.
_MAX_MAPS = 500


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------

# The scanner's contiguous-table data offset carries pad ambiguity (its pad
# guess can be off by 0/2/4 bytes — see the ±4 alignment search in
# diff_maps).  Annotation is therefore pad-tolerant: an instruction belongs
# to a map when it falls inside the map's structural region (X axis start →
# data end) widened by 4 bytes.  Axis-byte changes count as map edits.
# Compound (strided) halves are exempt — the split pass pins their data at
# Y-end, so their rows are exact.
_PAD_TOLERANCE = 4


def table_cell_rows(table: MapTable) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` byte range of each data row of *table*.

    For contiguous tables this is a single range (``rows`` handled by the
    caller); for compound halves the ranges are the interleaved rows.
    """
    row_bytes = table.cols * table.cell_width
    if table.stride is None or table.stride == row_bytes:
        return [(table.offset, table.offset + table.rows * row_bytes)]
    return [
        (table.offset + r * table.stride, table.offset + r * table.stride + row_bytes)
        for r in range(table.rows)
    ]


def instruction_hits_table(offset: int, size: int, table: MapTable) -> bool:
    """True when the byte range ``[offset, offset+size)`` belongs to *table*.

    Contiguous tables: pad-tolerant structural region (X axis → data end).
    Compound halves: exact per-row overlap.
    """
    row_bytes = table.cols * table.cell_width
    if table.stride is not None and table.stride != row_bytes:
        for r in range(table.rows):
            row_start = table.offset + r * table.stride
            if offset < row_start + row_bytes and offset + size > row_start:
                return True
        return False

    axis_start = (
        table.x_axis_offset
        if table.x_axis_offset is not None
        else table.offset
    )
    start = min(axis_start, table.offset) - _PAD_TOLERANCE
    end = table.offset + table.rows * row_bytes + _PAD_TOLERANCE
    return offset < end and offset + size > start


# ---------------------------------------------------------------------------
# Axis serialisation
# ---------------------------------------------------------------------------


def _read_axis_values(
    data: bytes, offset: int, length: int, byte_order: str
) -> list[int]:
    """Read u16 axis values from binary at *offset*."""
    le = byte_order == "little"
    fmt = f"{'<' if le else '>'}{length}H"
    return list(struct.unpack_from(fmt, data, offset))


def _axis_dict(
    data: bytes, offset: int | None, length: int, byte_order: str
) -> dict | None:
    if offset is None:
        return None
    return {
        "offset": offset,
        "values": _read_axis_values(data, offset, length, byte_order),
    }


# ---------------------------------------------------------------------------
# Core: attach maps to a recipe
# ---------------------------------------------------------------------------


def attach_maps(recipe: dict, stock_data: bytes, tables: list | None = None) -> dict:
    """
    Annotate *recipe* with a ``maps`` section and bump it to schema 4.4.

    Scans the stock binary for calibration tables (structural scan — no
    manufacturer database), greedily assigns each changed instruction to
    the highest-scoring table whose cell bytes contain it, classifies each
    kept table, and writes the result into ``recipe["maps"]``.

    Args:
        recipe:     A complete 4.3 recipe dict (as built by
                    ``ECUDiffAnalyzer.build_recipe``).  Modified in place
                    and also returned.
        stock_data: The original (stock) binary content the recipe was
                    cooked from.
        tables:     Optional precomputed ``scan_map_tables`` result (same
                    parameters as the internal default) — pass it to share
                    one scan between map annotation and other consumers
                    (``cook`` region tags) instead of scanning twice.

    Returns:
        The same recipe dict, now schema 4.4 with a ``maps`` list.
    """
    instructions = recipe.get("instructions", [])
    if tables is None:
        tables = scan_map_tables(
            stock_data,
            min_score=_SCAN_MIN_SCORE,
            max_series_tables=_SCAN_MAX_SERIES,
        )

    # Greedy assignment: tables are visited in descending score order (the
    # scanner returns them sorted); each instruction is claimed by the
    # first (highest-scoring) table whose cell bytes contain it.  Tables
    # left with zero refs are dropped — overlapping candidate tables cannot
    # double-count an instruction.
    fuel_type = family_fuel_type(recipe.get("ecu", {}).get("ecu_family"))
    maps: list[dict] = []
    claimed: set[int] = set()

    for table in tables:
        if len(maps) >= _MAX_MAPS:
            break
        refs = [
            idx
            for idx, inst in enumerate(instructions, 1)
            if idx not in claimed and instruction_hits_table(inst["offset"], inst["size"], table)
        ]
        if not refs:
            continue
        claimed.update(refs)

        label, label_confidence = classify_table(stock_data, table, fuel_type)
        maps.append(
            {
                "id": f"m{len(maps) + 1}",
                "offset": table.offset,
                "cols": table.cols,
                "rows": table.rows,
                "cell_width": table.cell_width,
                "byte_order": table.byte_order,
                "stride": table.stride,
                "x_axis": _axis_dict(
                    stock_data, table.x_axis_offset, table.cols, table.byte_order
                ),
                "y_axis": _axis_dict(
                    stock_data, table.y_axis_offset, table.rows, table.byte_order
                ),
                "score": round(table.score, 3),
                "label": label,
                "label_confidence": round(label_confidence, 2),
                "instruction_refs": refs,
            }
        )

    recipe["schema_version"] = MAPS_SCHEMA_VERSION
    recipe["maps"] = maps
    recipe["metadata"]["annotated_maps"] = True
    return recipe
