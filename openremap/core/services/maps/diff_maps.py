"""
openremap.core.services.maps.diff_maps — diff calibration maps between two binaries.

The whole matching/diff pipeline: two map scans + axis-fingerprint matching +
cell-by-cell diff + near-match + changed-block promotion.  Pure bytes/dict
in-out; the ``diff_maps`` RPC handler (path I/O) stays in the API gate.
"""

from __future__ import annotations

import struct
from typing import Sequence

from openremap._rust import (  # type: ignore[import-untyped]
    best_alignment as _rs_best_alignment,
    diff_cells as _rs_diff_cells,
    find_changed_blocks,
    read_cells as _rs_read_cells,
)
from openremap.core.services.maps.map_hunter import MapTable
from openremap.core.services.maps.scan_maps import scan_one

_PAD_SLACK = 4

_NEAR_MATCH_AXIS_DEV_RATIO = 0.15
_NEAR_MATCH_CELL_CORR = 0.95
_SUSPICIOUS_CORR = 0.7

_PROMOTE_MAX_ROWS = 64
_PROMOTE_MAX_COLS = 16


# ---------------------------------------------------------------------------
# Binary data reading
# ---------------------------------------------------------------------------


def _read_axis_values(
    data: bytes, offset: int, length: int, byte_order: str,
) -> tuple[int, ...]:
    le = byte_order == "little"
    fmt = f"{'<' if le else '>'}{length}H"
    return tuple(struct.unpack_from(fmt, data, offset))


def _read_cells(
    data: bytes,
    offset: int,
    cols: int,
    rows: int,
    cell_width: int,
    byte_order: str,
    stride: int | None = None,
) -> list[int]:
    return _rs_read_cells(data, offset, cols, rows, cell_width, byte_order, stride)


def _best_alignment(
    stock_data: bytes,
    tuned_data: bytes,
    stock_guess: int,
    tuned_guess: int,
    cols: int,
    rows: int,
    cell_width: int,
    byte_order: str,
    stride: int | None = None,
) -> tuple[list[int], list[int], int, int]:
    result = _rs_best_alignment(
        stock_data, tuned_data, stock_guess, tuned_guess,
        cols, rows, cell_width, byte_order, stride,
    )
    if result is None:
        raise IndexError("list index out of range")
    return result


# ---------------------------------------------------------------------------
# Fingerprint & matching
# ---------------------------------------------------------------------------


def _axis_fingerprint(data: bytes, t: MapTable) -> tuple:
    x_vals = _read_axis_values(data, t.x_axis_offset, t.cols, t.byte_order)
    if t.y_axis_offset is not None and t.rows > 1:
        y_vals = _read_axis_values(data, t.y_axis_offset, t.rows, t.byte_order)
        return (x_vals, y_vals)
    return (x_vals, ())


def _build_stock_index(
    data: bytes, tables: list[MapTable],
) -> dict[tuple, list[MapTable]]:
    index: dict[tuple, list[MapTable]] = {}
    for t in tables:
        fp = _axis_fingerprint(data, t)
        index.setdefault(fp, []).append(t)
    return index


# ---------------------------------------------------------------------------
# Correlation & near-match
# ---------------------------------------------------------------------------


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy) ** 0.5


def _axes_similar(
    a: tuple[int, ...],
    b: tuple[int, ...],
    max_dev_ratio: float,
) -> bool:
    if len(a) != len(b) or not a:
        return False
    span = max(max(a), max(b))
    if span == 0:
        return a == b
    dev = max(abs(x - y) for x, y in zip(a, b))
    return dev / span <= max_dev_ratio


def _near_match_pass(
    stock_data: bytes,
    tuned_data: bytes,
    stock_tables: list[MapTable],
    used_stock_offsets: set[int],
    unmatched_tuned: list[MapTable],
    axis_dev_ratio: float = _NEAR_MATCH_AXIS_DEV_RATIO,
    cell_corr: float = _NEAR_MATCH_CELL_CORR,
) -> tuple[list[MapTable], list[dict]]:
    remaining = [
        t for t in stock_tables if t.offset not in used_stock_offsets
    ]
    shape_index: dict[tuple, list[MapTable]] = {}
    for st in remaining:
        key = (st.cols, st.rows, st.cell_width, st.byte_order, st.stride)
        shape_index.setdefault(key, []).append(st)

    still_unmatched: list[MapTable] = []
    near_matches: list[dict] = []

    for tt in unmatched_tuned:
        best: (
            tuple[float, MapTable, list[int], list[int], int, int] | None
        ) = None
        candidates = shape_index.get(
            (tt.cols, tt.rows, tt.cell_width, tt.byte_order, tt.stride),
            (),
        )
        for st in candidates:
            if st.offset in used_stock_offsets:
                continue
            if st.x_axis_offset is None or tt.x_axis_offset is None:
                continue

            sx = _read_axis_values(
                stock_data, st.x_axis_offset, st.cols, st.byte_order,
            )
            tx = _read_axis_values(
                tuned_data, tt.x_axis_offset, tt.cols, tt.byte_order,
            )
            if not _axes_similar(sx, tx, axis_dev_ratio):
                continue
            if st.y_axis_offset is not None and st.rows > 1:
                if tt.y_axis_offset is None:
                    continue
                sy = _read_axis_values(
                    stock_data, st.y_axis_offset, st.rows, st.byte_order,
                )
                ty = _read_axis_values(
                    tuned_data, tt.y_axis_offset, tt.rows, tt.byte_order,
                )
                if not _axes_similar(sy, ty, axis_dev_ratio):
                    continue

            if st.stride is not None:
                stock_cells = _read_cells(
                    stock_data, st.offset, st.cols, st.rows,
                    st.cell_width, st.byte_order, st.stride,
                )
                tuned_cells = _read_cells(
                    tuned_data, tt.offset, tt.cols, tt.rows,
                    tt.cell_width, tt.byte_order, tt.stride,
                )
                so, to = st.offset, tt.offset
            else:
                stock_cells, tuned_cells, so, to = _best_alignment(
                    stock_data, tuned_data,
                    st.offset, tt.offset,
                    st.cols, st.rows, st.cell_width, st.byte_order,
                )
            r = _pearson(stock_cells, tuned_cells)
            if r is None or r < cell_corr:
                continue
            if best is None or r > best[0]:
                best = (r, st, stock_cells, tuned_cells, so, to)

        if best is None:
            still_unmatched.append(tt)
            continue

        r, st, stock_cells, tuned_cells, so, to = best
        used_stock_offsets.add(st.offset)

        sx = _read_axis_values(
            stock_data, st.x_axis_offset, st.cols, st.byte_order,
        )
        tx = _read_axis_values(
            tuned_data, tt.x_axis_offset, tt.cols, tt.byte_order,
        )
        sy: tuple[int, ...] = ()
        ty: tuple[int, ...] = ()
        if st.y_axis_offset is not None and st.rows > 1:
            sy = _read_axis_values(
                stock_data, st.y_axis_offset, st.rows, st.byte_order,
            )
            ty = _read_axis_values(
                tuned_data, tt.y_axis_offset, tt.rows, tt.byte_order,
            )

        diff = _diff_cells(stock_cells, tuned_cells)
        suspicious = False

        near_matches.append({
            "offset_stock": so,
            "offset_tuned": to,
            "cols": st.cols,
            "rows": st.rows,
            "cell_width": st.cell_width,
            "byte_order": st.byte_order,
            "stride": st.stride,
            "offset_delta": to - so,
            "realigned": so != st.offset or to != tt.offset,
            "suspicious": suspicious,
            "correlation": round(r, 4),
            "near_match": True,
            "axis_changed": True,
            "axis_stock": {"x": list(sx), "y": list(sy)},
            "axis_tuned": {"x": list(tx), "y": list(ty)},
            "_fp": (sx, sy),
            "_stock_table": st,
            "_tuned_table": tt,
            **diff,
        })

    return still_unmatched, near_matches


# ---------------------------------------------------------------------------
# Cell-by-cell diff
# ---------------------------------------------------------------------------


def _diff_cells(stock_cells: list[int], tuned_cells: list[int]) -> dict:
    if len(stock_cells) != len(tuned_cells):
        return {
            "error": (
                f"cell count mismatch: "
                f"{len(stock_cells)} vs {len(tuned_cells)}"
            ),
        }

    max_abs, avg_abs, max_pct, avg_pct, changed, total = _rs_diff_cells(
        stock_cells, tuned_cells,
    )
    return {
        "max_abs": round(max_abs, 2),
        "avg_abs": round(avg_abs, 2),
        "max_pct": max_pct if max_pct in (float("inf"), float("-inf")) else round(max_pct, 2),
        "avg_pct": avg_pct if avg_pct in (float("inf"), float("-inf")) else round(avg_pct, 2),
        "changed_cells": changed,
        "total_cells": total,
    }


def _annotate_matches(
    matches: list[dict],
    stock_data: bytes,
    tuned_data: bytes,
    recipe: dict | None,
) -> dict:
    if recipe is None:
        return {}

    instructions = sorted(
        (i["offset"], i["offset"] + i.get("size", 0))
        for i in recipe.get("instructions", [])
        if i.get("size", 0) > 0
    )
    if not instructions:
        return {}

    maps_touched = 0
    covered_total = 0
    untracked_total = 0
    instr_hits_total = 0

    for m in matches:
        cols, rows, cw = m["cols"], m["rows"], m["cell_width"]
        stride = m.get("stride")
        row_bytes = cols * cw
        eff_stride = stride if stride is not None else row_bytes

        stock_cells = _read_cells(
            stock_data, m["offset_stock"], cols, rows, cw,
            m["byte_order"], stride,
        )
        tuned_cells = _read_cells(
            tuned_data, m["offset_tuned"], cols, rows, cw,
            m["byte_order"], stride,
        )

        covered: set[int] = set()
        hits = 0
        off = m["offset_stock"]
        for r in range(rows):
            row_start = off + r * eff_stride
            row_end = row_start + row_bytes
            for os_, oe in instructions:
                if oe <= row_start:
                    continue
                if os_ >= row_end:
                    break
                hits += 1
                cs = max(0, (os_ - row_start) // cw)
                ce = min(cols - 1, (oe - 1 - row_start) // cw)
                for c in range(cs, ce + 1):
                    covered.add(r * cols + c)

        covered_changed = sum(
            1
            for idx in covered
            if idx < len(stock_cells)
            and stock_cells[idx] != tuned_cells[idx]
        )
        changed = m.get("changed_cells", 0)
        untracked = max(0, changed - covered_changed)

        m["recipe_instr_hits"] = hits
        m["recipe_cells_covered"] = covered_changed
        m["untracked_cells"] = untracked
        if hits:
            maps_touched += 1
        covered_total += covered_changed
        untracked_total += untracked
        instr_hits_total += hits

    return {
        "file": recipe.get("metadata", {}).get("name", ""),
        "instructions": len(instructions),
        "maps_touched": maps_touched,
        "covered_changed_cells": covered_total,
        "untracked_changed_cells": untracked_total,
        "instr_hits": instr_hits_total,
    }


# ---------------------------------------------------------------------------
# Changed-block promotion
# ---------------------------------------------------------------------------


def _read_u16_row(data: bytes, offset: int, k: int, le: bool) -> tuple[int, ...]:
    fmt = f"{'<' if le else '>'}{k}H"
    return tuple(struct.unpack_from(fmt, data, offset))


def _plausible_changed_row(row: tuple[int, ...]) -> bool:
    if len(row) < 4:
        return False
    if all(v == 0 for v in row) or all(v == 0xFFFF for v in row):
        return False
    return all(v <= 0xF000 for v in row)


def _repeated_row_table(
    stock_data: bytes,
    tuned_data: bytes,
    off: int,
    size: int,
) -> tuple[int, int, str, list[int], list[int]] | None:
    for le in (True, False):
        for k in range(4, _PROMOTE_MAX_COLS + 1):
            row_bytes = k * 2
            if size < row_bytes * 3:
                continue
            row0 = _read_u16_row(stock_data, off, k, le)
            if not _plausible_changed_row(row0):
                continue
            n = 1
            while (
                n < _PROMOTE_MAX_ROWS
                and (n + 1) * row_bytes <= size
                and _read_u16_row(stock_data, off + n * row_bytes, k, le) == row0
            ):
                n += 1
            if n < 3:
                continue
            tuned_size = min(size, len(tuned_data) - off)
            n_tuned = min(n, tuned_size // row_bytes)
            if n_tuned < 3:
                continue
            bo = "little" if le else "big"
            stock_cells = _read_cells(
                stock_data, off, k, n, 2, bo,
            )
            tuned_cells = _read_cells(
                tuned_data, off, k, n_tuned, 2, bo,
            )
            if len(stock_cells) != len(tuned_cells):
                continue
            return k, n, bo, stock_cells, tuned_cells
    return None


def _covered_spans(matches: list[dict]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in matches:
        cols, rows, cw = m["cols"], m["rows"], m["cell_width"]
        stride = m.get("stride")
        if stride is None:
            span = (m["offset_stock"], m["offset_stock"] + cols * rows * cw)
        else:
            span = (
                m["offset_stock"],
                m["offset_stock"] + (rows - 1) * stride + cols * cw,
            )
        start, end = span
        spans.append((max(0, start - _PAD_SLACK), end + _PAD_SLACK))
    return spans


def _unidentified_changed_blocks(
    blocks: list, matches: list[dict],
) -> list[dict]:
    covered = _covered_spans(matches)
    out: list[dict] = []
    for off, size, _ob, _mb in blocks:
        end = off + size
        uncovered: list[tuple[int, int]] = [(off, end)]
        for ds, de in sorted(covered):
            if de <= off or ds >= end:
                continue
            split: list[tuple[int, int]] = []
            for us, ue in uncovered:
                if ds >= ue or de <= us:
                    split.append((us, ue))
                else:
                    if us < ds:
                        split.append((us, min(ue, ds)))
                    if ue > de:
                        split.append((max(us, de), ue))
            uncovered = split
        for us, ue in uncovered:
            if ue > us:
                out.append({"offset": us, "size": ue - us})
    return out


def _promote_uncovered_changed_blocks(
    stock_data: bytes,
    tuned_data: bytes,
    blocks: list,
    matches: list[dict],
    limit: int = 64,
) -> list[dict]:
    covered = _covered_spans(matches)

    promoted: list[dict] = []
    for off, size, _ob, _mb in blocks:
        end = off + size
        if any(off < de and ds < end for ds, de in covered):
            continue
        if end > len(tuned_data):
            continue
        res = _repeated_row_table(stock_data, tuned_data, off, size)
        if res is None:
            continue
        cols, rows, byte_order, stock_cells, tuned_cells = res
        diff = _diff_cells(stock_cells, tuned_cells)
        promoted.append({
            "offset_stock": off,
            "offset_tuned": off,
            "cols": cols,
            "rows": rows,
            "cell_width": 2,
            "byte_order": byte_order,
            "stride": None,
            "offset_delta": 0,
            "realigned": False,
            "suspicious": False,
            "correlation": None,
            "promoted": True,
            "_fp": ((), ()),
            "_stock_table": None,
            "_tuned_table": None,
            **diff,
        })
        if len(promoted) >= limit:
            break
    return promoted


def _json_safe(m: dict) -> dict:
    clean = dict(m)
    for key in ("max_pct", "avg_pct"):
        val = clean.get(key)
        if isinstance(val, float) and val == float("inf"):
            clean[key] = "inf"
        elif isinstance(val, float) and val == float("-inf"):
            clean[key] = "-inf"
    return clean


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def diff_maps_binary(
    stock_data: bytes,
    tuned_data: bytes,
    *,
    region: slice | None = None,
    min_score: float = 0.55,
    max_series_tables: int = 16,
    layout_default: bool = True,
    recipe_data: dict | None = None,
) -> dict:
    """Run the full diff-maps pipeline, returning the raw intermediate result."""
    import time

    changed_blocks = find_changed_blocks(stock_data, tuned_data, 16)

    t0 = time.perf_counter()
    stock_result = scan_one(
        stock_data, region, min_score, max_series_tables,
        layout_default=layout_default,
    )
    tuned_result = scan_one(
        tuned_data, region, min_score, max_series_tables,
        layout_default=layout_default,
    )
    elapsed_scan = time.perf_counter() - t0

    stock_tables: list[MapTable] = stock_result["tables"]
    tuned_tables: list[MapTable] = tuned_result["tables"]

    stock_index = _build_stock_index(stock_data, stock_tables)

    matches: list[dict] = []
    unmatched_tuned: list[MapTable] = []
    used_stock_offsets: set[int] = set()

    for tt in tuned_tables:
        fp = _axis_fingerprint(tuned_data, tt)
        candidates = stock_index.get(fp, [])

        available = [
            st for st in candidates if st.offset not in used_stock_offsets
        ]

        if not available:
            unmatched_tuned.append(tt)
            continue

        best = min(available, key=lambda st: abs(st.offset - tt.offset))
        used_stock_offsets.add(best.offset)

        if best.stride is not None:
            so, to = best.offset, tt.offset
            stock_cells = _read_cells(
                stock_data, so, best.cols, best.rows,
                best.cell_width, best.byte_order, best.stride,
            )
            tuned_cells = _read_cells(
                tuned_data, to, tt.cols, tt.rows,
                tt.cell_width, tt.byte_order, tt.stride,
            )
        else:
            stock_cells, tuned_cells, so, to = _best_alignment(
                stock_data, tuned_data,
                best.offset, tt.offset,
                best.cols, best.rows, best.cell_width, best.byte_order,
            )

        diff = _diff_cells(stock_cells, tuned_cells)
        total_cells = diff.get("total_cells", 0)
        changed_cells = diff.get("changed_cells", 0)

        realigned = so != best.offset or to != tt.offset
        correlation = _pearson(stock_cells, tuned_cells)
        suspicious = (
            total_cells > 0
            and changed_cells > 0.9 * total_cells
            and (correlation is None or correlation < _SUSPICIOUS_CORR)
        )

        matches.append({
            "offset_stock": so,
            "offset_tuned": to,
            "cols": best.cols,
            "rows": best.rows,
            "cell_width": best.cell_width,
            "byte_order": best.byte_order,
            "stride": best.stride,
            "offset_delta": to - so,
            "realigned": realigned,
            "suspicious": suspicious,
            "correlation": (
                round(correlation, 4) if correlation is not None else None
            ),
            "_fp": fp,
            "_stock_table": best,
            "_tuned_table": tt,
            **diff,
        })

    unmatched_tuned, near_matches = _near_match_pass(
        stock_data, tuned_data,
        stock_tables, used_stock_offsets, unmatched_tuned,
    )
    matches.extend(near_matches)

    unmatched_stock = [
        t
        for t in stock_tables
        if t.offset not in used_stock_offsets
    ]

    matches.extend(
        _promote_uncovered_changed_blocks(
            stock_data, tuned_data, changed_blocks, matches,
        )
    )

    unidentified = _unidentified_changed_blocks(changed_blocks, matches)

    def _sort_key(m: dict) -> float:
        val = m["max_abs"]
        if isinstance(val, float) and val == float("inf"):
            return float("inf")
        return float(val)

    matches.sort(key=_sort_key, reverse=True)

    recipe_summary: dict = {}
    if recipe_data is not None:
        recipe_summary = _annotate_matches(
            matches, stock_data, tuned_data, recipe_data,
        )

    return {
        "matches": matches,
        "unmatched_stock": unmatched_stock,
        "unmatched_tuned": unmatched_tuned,
        "unidentified": unidentified,
        "stock_result": stock_result,
        "tuned_result": tuned_result,
        "recipe_summary": recipe_summary,
        "stock_size": len(stock_data),
        "tuned_size": len(tuned_data),
        "elapsed_scan": elapsed_scan,
    }


def _group_id(index: int) -> str:
    s = ""
    n = index
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s


def build_diff_json(
    stock_label: str,
    tuned_label: str,
    result: dict,
    *,
    threshold: float = 0.0,
    top: int = 50,
    recipe_label: str | None = None,
) -> dict:
    """Build the CLI ``--json``-shaped dict from a pipeline result."""
    from collections import defaultdict as _defaultdict

    matches = result["matches"]
    stock_tables = result["stock_result"]["tables"]
    tuned_tables = result["tuned_result"]["tables"]
    unmatched_stock = result["unmatched_stock"]
    unmatched_tuned = result["unmatched_tuned"]
    unidentified = result["unidentified"]
    recipe_summary = dict(result["recipe_summary"])

    def _sort_key(m: dict) -> float:
        val = m["max_abs"]
        if isinstance(val, float) and val == float("inf"):
            return float("inf")
        return float(val)

    above_threshold = [
        m for m in matches
        if (
            isinstance(m["max_abs"], float) and m["max_abs"] == float("inf")
        ) or m["max_abs"] >= threshold
    ]
    shown = above_threshold[:top]

    jgroups: dict[tuple, list[dict]] = _defaultdict(list)
    for m in shown:
        jgroups[m["_fp"]].append(m)
    jgroups_sorted = sorted(
        jgroups.items(), key=lambda kv: _sort_key(kv[1][0]), reverse=True,
    )

    group_index: dict[int, int] = {}
    groups_summary = []
    for gi, (fp, members) in enumerate(jgroups_sorted):
        x_vals, y_vals = fp
        for m in members:
            group_index[id(m)] = gi
        anchor = members[0]
        groups_summary.append({
            "id": _group_id(gi),
            "count": len(members),
            "cols": anchor["cols"],
            "rows": anchor["rows"],
            "cell_width": anchor["cell_width"],
            "byte_order": anchor["byte_order"],
            "x_axis": list(x_vals),
            "y_axis": list(y_vals),
        })

    json_matches = []
    for m in shown:
        clean = dict(m)
        clean.pop("_stock_table", None)
        clean.pop("_tuned_table", None)
        clean.pop("_fp", None)
        clean["group"] = group_index[id(m)]
        json_matches.append(_json_safe(clean))

    if recipe_summary and recipe_label:
        recipe_summary["file"] = recipe_label

    return {
        "stock": stock_label,
        "tuned": tuned_label,
        "recipe": recipe_summary or None,
        "stock_size": result["stock_size"],
        "tuned_size": result["tuned_size"],
        "stock_tables": len(stock_tables),
        "tuned_tables": len(tuned_tables),
        "stock_tables_hidden": result["stock_result"].get("tables_hidden", 0),
        "tuned_tables_hidden": result["tuned_result"].get("tables_hidden", 0),
        "matched_count": len(matches),
        "above_threshold": len(above_threshold),
        "only_in_stock_count": len(unmatched_stock),
        "only_in_tuned_count": len(unmatched_tuned),
        "unidentified_changed_count": len(unidentified),
        "unidentified_changed": unidentified[:200],
        "scan_seconds": round(result["elapsed_scan"], 2),
        "groups": groups_summary,
        "matches": json_matches,
        "only_in_stock": [
            {
                "offset": t.offset,
                "cols": t.cols,
                "rows": t.rows,
                "cell_width": t.cell_width,
                "byte_order": t.byte_order,
            }
            for t in unmatched_stock[:200]
        ],
        "only_in_tuned": [
            {
                "offset": t.offset,
                "cols": t.cols,
                "rows": t.rows,
                "cell_width": t.cell_width,
                "byte_order": t.byte_order,
            }
            for t in unmatched_tuned[:200]
        ],
    }
