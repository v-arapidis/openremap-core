"""
openremap diff-maps <stock.bin> <tuned.bin>

Find matching calibration maps between two ECU binaries by axis fingerprint
and diff them cell-by-cell.

Examples:
    openremap diff-maps stock.bin tuned.bin
    openremap diff-maps stock.bin tuned.bin --threshold 5.0
    openremap diff-maps stock.bin tuned.bin --json
    openremap diff-maps stock.bin tuned.bin --top 20
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

from typing import Sequence

import typer

from openremap._rust import find_changed_blocks  # type: ignore[import-untyped]
from openremap.cli.commands.scan_maps import (
    _parse_region,
    _scan_one,
)
from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EXTENSIONS = {".bin", ".ori", ".hex"}
def _bo_label(byte_order: str) -> str:
    """``'little'`` → ``'LE'``, ``'big'`` → ``'BE'``."""
    return "LE" if byte_order == "little" else "BE"



# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _read_bin(path: Path, label: str) -> bytes:
    """Read and validate a single ECU binary file."""
    if path.suffix.lower() not in VALID_EXTENSIONS:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, or .hex file.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        data = path.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error reading file: {exc}", fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    if not data:
        typer.echo(
            typer.style(
                f"Error: '{path.name}' is empty.", fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    return data


# ---------------------------------------------------------------------------
# Binary data reading (mirrors map_exporter.py pattern)
# ---------------------------------------------------------------------------

# The pad-search range _best_alignment explores around both guessed
# offsets — also the coverage slack for "changed but not identified".
_PAD_SLACK = 4


def _read_axis_values(
    data: bytes, offset: int, length: int, byte_order: str,
) -> tuple[int, ...]:
    """Read u16 axis values from binary at *offset*."""
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
    """Read cell values from the data block at *offset*.

    *stride* is the bytes-per-row of a compound (interleaved) table half;
    ``None`` means contiguous rows.
    """
    le = byte_order == "little"
    count = cols * rows
    row_bytes = cols * cell_width
    if stride is None or stride == row_bytes:
        if cell_width == 1:
            return list(data[offset : offset + count])
        fmt = f"{'<' if le else '>'}{count}H"
        return list(struct.unpack_from(fmt, data, offset))

    out: list[int] = []
    for r in range(rows):
        row_off = offset + r * stride
        if cell_width == 1:
            out.extend(data[row_off : row_off + cols])
        else:
            fmt = f"{'<' if le else '>'}{cols}H"
            out.extend(struct.unpack_from(fmt, data, row_off))
    return out


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
    """Find the grid alignment with the fewest changed cells.

    The scanner guesses each table's data offset via axis pairing and its
    padding choice (0/2/4 bytes) can differ between two nearly-identical
    binaries.  Trying all pad variants (±4 bytes) around both guesses and
    keeping the alignment that minimises changed cells makes the diff
    robust to that ambiguity — a real tune changes a minority of cells.

    Returns ``(stock_cells, tuned_cells, stock_offset, tuned_offset)``.
    """
    count = cols * rows
    row_bytes = cols * cell_width
    if stride is None:
        span = count * cell_width
    else:
        span = (rows - 1) * stride + row_bytes

    candidates: list[tuple[int, int, int, int]] = []
    for s_shift in (-4, -2, 0, 2, 4):
        so = stock_guess + s_shift
        if so < 0 or so + span > len(stock_data):
            continue
        stock_cells = _read_cells(
            stock_data, so, cols, rows, cell_width, byte_order, stride,
        )
        for t_shift in (-4, -2, 0, 2, 4):
            to = tuned_guess + t_shift
            if to < 0 or to + span > len(tuned_data):
                continue
            tuned_cells = _read_cells(
                tuned_data, to, cols, rows, cell_width, byte_order, stride,
            )
            changed = sum(
                1 for s, t in zip(stock_cells, tuned_cells) if s != t
            )
            # Fewest changes wins; ties prefer the scanner's own guesses.
            distance = abs(so - stock_guess) + abs(to - tuned_guess)
            candidates.append((changed, distance, so, to))

    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
    _changed, _distance, so, to = candidates[0]
    return (
        _read_cells(stock_data, so, cols, rows, cell_width, byte_order, stride),
        _read_cells(tuned_data, to, cols, rows, cell_width, byte_order, stride),
        so,
        to,
    )


# ---------------------------------------------------------------------------
# Fingerprint & matching
# ---------------------------------------------------------------------------


def _axis_fingerprint(data: bytes, t: MapTable) -> tuple:
    """Create a hashable fingerprint from a table's axis value tuples.

    Returns ``(x_vals, y_vals)`` where each element is a ``tuple[int, ...]``.
    For 1D tables (no Y axis), ``y_vals`` is an empty tuple.
    """
    x_vals = _read_axis_values(data, t.x_axis_offset, t.cols, t.byte_order)
    if t.y_axis_offset is not None and t.rows > 1:
        y_vals = _read_axis_values(data, t.y_axis_offset, t.rows, t.byte_order)
        return (x_vals, y_vals)
    return (x_vals, ())


def _build_stock_index(
    data: bytes, tables: list[MapTable],
) -> dict[tuple, list[MapTable]]:
    """Index stock tables by axis fingerprint → list (handles collisions).

    Multiple maps often share the same axis breakpoints (same RPM column
    drives fuel, timing, and boost maps).  We store all candidates and
    disambiguate by offset proximity during matching.
    """
    index: dict[tuple, list[MapTable]] = {}
    for t in tables:
        fp = _axis_fingerprint(data, t)
        index.setdefault(fp, []).append(t)
    return index


# ---------------------------------------------------------------------------
# Correlation & near-match — tables whose axis breakpoints changed
# ---------------------------------------------------------------------------

# A tuner editing a map's axis values (moving RPM/load breakpoints) makes
# the exact fingerprint match fail.  These constants bound the second
# matching pass: axes must stay close (normalised deviation) and the cell
# grids must correlate strongly.
_NEAR_MATCH_AXIS_DEV_RATIO = 0.15
_NEAR_MATCH_CELL_CORR = 0.95
# A match with >90% changed cells is "suspicious" only when the grids do
# NOT correlate: a heavily retuned map still looks like itself (high r),
# while two different maps sharing axes look unrelated (low r).
_SUSPICIOUS_CORR = 0.7


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson correlation coefficient, or None when undefined.

    Returns None for mismatched lengths, fewer than two points, or a
    constant input (zero variance) — correlation is meaningless there.
    """
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
    """Same-length axis tuples whose values stay within a deviation ratio.

    "Changed axes" means the tuner edited the breakpoints, not that this
    is a different axis: the values remain close.  A correlation check
    would be useless here — any two monotone sequences correlate ~1.0 —
    so we use normalised max deviation.  NOTE: the denominator is the
    larger axis MAXIMUM (``max(max(a), max(b))``), not the value range —
    for axes with large absolute values but a narrow range the effective
    tolerance is looser than ``max_dev_ratio`` of the range suggests.
    The correlation gate (``r >= cell_corr``) is the real safeguard.
    """
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
    """Second matching pass: pair up tables whose axis breakpoints changed.

    Exact fingerprint matching (pass 1) drops a map into the only-in-*
    lists when the tuner edited its axis values.  Here, a stock table with
    the same shape whose axes are close and whose cell grid correlates
    strongly is almost certainly the same map — report it as a match
    flagged ``near_match``/``axis_changed`` instead of leaving it silent.

    One-to-one: each stock table is consumed at most once, and exact
    matches (pass 1) always win because this pass only sees tables pass 1
    could not pair.

    Returns ``(still_unmatched, near_matches)``.
    """
    remaining = [
        t for t in stock_tables if t.offset not in used_stock_offsets
    ]
    # Pre-index remaining stock tables by shape — the shape guard below is
    # exact equality, so only same-shape candidates can ever match.  This
    # turns the worst case O(unmatched × remaining) into O(unmatched ×
    # same-shape-candidates): a tune that rescales MANY axes (which makes
    # the exact pass fail broadly) no longer blows up quadratically.
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
                # Compound (strided) tables: mirror the exact-match path —
                # read each half with its stride and trust the scanner
                # offsets (the Rust split pass pinned them structurally).
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
        # A near-match is by definition strongly correlated (r >= cell_corr),
        # so it can never be the "two different maps sharing axes" case the
        # suspicious flag describes — always False by construction.
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
    """Compute cell-by-cell diff statistics between two cell lists."""
    if len(stock_cells) != len(tuned_cells):
        return {
            "error": (
                f"cell count mismatch: "
                f"{len(stock_cells)} vs {len(tuned_cells)}"
            ),
        }

    total = len(stock_cells)
    abs_diffs = [abs(t - s) for s, t in zip(stock_cells, tuned_cells)]
    changed = sum(1 for d in abs_diffs if d > 0)

    max_abs = float(max(abs_diffs)) if abs_diffs else 0.0
    avg_abs = sum(abs_diffs) / total if total else 0.0

    # Percentage changes — handle division by zero.
    # stock=0, tuned≠0  →  ±inf;  stock=0, tuned=0  →  0.0.
    pct_diffs: list[float] = []
    for s, t in zip(stock_cells, tuned_cells):
        if s != 0:
            pct_diffs.append((t - s) / s * 100)
        elif t != 0:
            pct_diffs.append(float("inf") if t > s else float("-inf"))
        else:
            pct_diffs.append(0.0)

    finite = [
        d for d in pct_diffs if d != float("inf") and d != float("-inf")
    ]

    # max_pct: largest by absolute magnitude (inf wins)
    max_pct: float = 0.0
    for d in pct_diffs:
        if d == float("inf") or d == float("-inf"):
            max_pct = d
            break
        if abs(d) > abs(max_pct):
            max_pct = d

    avg_pct: float
    if finite:
        avg_pct = sum(abs(d) for d in finite) / len(finite)
    elif pct_diffs:
        # Every cell went 0 → nonzero (disabled → enabled): the average
        # percentage change is unbounded — reporting 0.0 would be a lie.
        avg_pct = float("inf")
    else:
        avg_pct = 0.0

    return {
        "max_abs": round(max_abs, 2),
        "avg_abs": round(avg_abs, 2),
        "max_pct": max_pct if max_pct in (float("inf"), float("-inf")) else round(max_pct, 2),
        "avg_pct": avg_pct if avg_pct in (float("inf"), float("-inf")) else round(avg_pct, 2),
        "changed_cells": changed,
        "total_cells": total,
    }


def _load_recipe(path: Path) -> dict:
    """Load a .remap recipe file; exit 1 when unreadable or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(
            typer.style(
                f"Error: cannot read recipe '{path.name}': {exc}",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    if not isinstance(data, dict) or "instructions" not in data:
        typer.echo(
            typer.style(
                f"Error: '{path.name}' is not a valid .remap recipe "
                f"(no 'instructions' field).",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    return data


def _annotate_matches(
    matches: list[dict],
    stock_data: bytes,
    tuned_data: bytes,
    recipe: dict | None,
) -> dict:
    """Cross-reference recipe instructions against matched maps.

    For every match, marks how many of its changed cells are covered by
    recipe instructions (byte-range overlap on the stock side) and how
    many changed cells are NOT covered (untracked changes).

    Returns aggregate counters for the report.
    """
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
# Display helpers
# ---------------------------------------------------------------------------


def _dim_str(t: MapTable) -> str:
    """``16×16``"""
    return f"{t.cols}×{t.rows}"


def _pct_str(value: float) -> str:
    """Format a percentage value for display, handling inf."""
    if value == float("inf"):
        return "    +∞"
    if value == float("-inf"):
        return "    -∞"
    return f"{value:>+7.1f}%"


# ---------------------------------------------------------------------------
# Changed-block promotion — tables the axis scanner cannot see
# ---------------------------------------------------------------------------

_PROMOTE_MAX_ROWS = 64
_PROMOTE_MAX_COLS = 16


def _read_u16_row(data: bytes, offset: int, k: int, le: bool) -> tuple[int, ...]:
    fmt = f"{'<' if le else '>'}{k}H"
    return tuple(struct.unpack_from(fmt, data, offset))


def _plausible_changed_row(row: tuple[int, ...]) -> bool:
    """A promoted row just needs to look like data, not an axis."""
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
    """Detect a changed block that is a repeated-row table.

    Returns ``(cols, rows, byte_order, stock_cells, tuned_cells)`` or
    ``None``.  Handles flat-Y tables (identical rows) with no adjacent
    axis — the axis scanner cannot see these, but a tuner editing them
    reveals their shape.
    """
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
    """Stock-side byte spans covered by matched tables (inclusive-exclusive).

    Each span is padded by the pad-search slack (±4 bytes — the range
    ``_best_alignment`` explores), so a matched table's alignment drift
    never turns its own changed cells into "unidentified" tails.
    """
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
    """Changed blocks not covered by any matched table.

    These are bytes that differ between stock and tuned but were not
    recognised as calibration tables — the "changed but not identified"
    audit gap.  Reporting them makes the diff complete: every changed
    byte is either a matched map, a promoted table, or listed here.

    Coverage is per-byte: a changed block that straddles a matched
    table's edge reports only its uncovered sub-ranges rather than being
    dropped or reported whole.
    """
    covered = _covered_spans(matches)
    out: list[dict] = []
    for off, size, _ob, _mb in blocks:
        end = off + size
        # Subtract covered spans from the block -> uncovered sub-ranges.
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
    """Promote changed byte blocks not covered by any matched table.

    A changed block that is a repeated-row pattern is almost certainly a
    calibration table the axis scanner missed (flat-Y layouts, missing
    breakpoints, constant-value maps).  Build a synthetic match so the
    diff reports it instead of staying silent.
    """
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
        total = diff.get("total_cells", 0)
        changed = diff.get("changed_cells", 0)
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


def _group_id(index: int) -> str:
    """0-based index → letter group id: A..Z, AA..AZ, …"""
    s = ""
    n = index
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s


def _axis_preview(vals: tuple[int, ...], limit: int = 4) -> str:
    """Short human-readable axis preview: ``680, 685, 810, 925…+4``"""
    shown = ", ".join(str(v) for v in vals[:limit])
    if len(vals) > limit:
        shown += f"…+{len(vals) - limit}"
    return shown


def _print_map_grids(
    stock_data: bytes,
    tuned_data: bytes,
    m: dict,
    stock_table: MapTable,
) -> None:
    """Print before (stock) and diff (tuned − stock) grids for one map.

    Changed cells in the diff grid are highlighted yellow; zeros are dimmed.
    Axis values are shown as column/row headers.
    """
    cols = m["cols"]
    rows = m["rows"]
    bo = m["byte_order"]
    is_1d = rows == 1

    # Read axis values (promoted tables have none)
    x_vals: tuple[int, ...] = ()
    if stock_table is not None and stock_table.x_axis_offset is not None:
        x_vals = _read_axis_values(stock_data, stock_table.x_axis_offset, cols, bo)
    y_vals: tuple[int, ...] = ()
    if not is_1d and stock_table is not None and stock_table.y_axis_offset is not None:
        y_vals = _read_axis_values(stock_data, stock_table.y_axis_offset, rows, bo)

    # Read cells
    stock_cells = _read_cells(
        stock_data, m["offset_stock"], cols, rows, m["cell_width"], bo,
        m.get("stride"),
    )
    tuned_cells = _read_cells(
        tuned_data, m["offset_tuned"], cols, rows, m["cell_width"], bo,
        m.get("stride"),
    )
    diffs = [t - s for s, t in zip(stock_cells, tuned_cells)]

    cell_w = 6  # column width for values

    typer.echo("")
    typer.echo(
        typer.style(
            f"  Map 0x{m['offset_stock']:08X}  {cols}×{rows}  "
            f"u{m['cell_width'] * 8} {_bo_label(bo)}",
            bold=True,
        ),
    )

    # ── Stock grid ──────────────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  Stock:", fg=typer.colors.CYAN))

    # Column headers (X axis)
    if is_1d:
        pass  # 1D: no column header row
    else:
        line = "        "  # indent for Y axis label column
        for x in x_vals[:16]:
            line += f"{x:>{cell_w}}"
        if cols > 16:
            line += f"  … +{cols - 16}"
        typer.echo(typer.style(line, dim=True))

    # Rows
    for r in range(min(rows, 20)):
        if is_1d:
            # 1D: two-column format
            val = stock_cells[r]
            typer.echo(f"  {x_vals[r]:>6}  {val:>{cell_w}}")
        else:
            prefix = f"  {y_vals[r]:>6}" if r < len(y_vals) else "        "
            line = prefix
            for c in range(min(cols, 16)):
                val = stock_cells[r * cols + c]
                line += f"{val:>{cell_w}}"
            if cols > 16:
                line += f"  … +{cols - 16}"
            typer.echo(line)

    if rows > 20:
        typer.echo(typer.style(f"  … +{rows - 20} rows", dim=True))

    # ── Diff grid ───────────────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  Diff (tuned − stock):", fg=typer.colors.CYAN))

    # Column headers
    if not is_1d:
        line = "        "
        for x in x_vals[:16]:
            line += f"{x:>{cell_w}}"
        if cols > 16:
            line += f"  … +{cols - 16}"
        typer.echo(typer.style(line, dim=True))

    # Rows
    for r in range(min(rows, 20)):
        if is_1d:
            d = diffs[r]
            d_str = f"{d:>+{cell_w}}"
            if d != 0:
                d_str = typer.style(d_str, fg=typer.colors.YELLOW, bold=True)
            else:
                d_str = typer.style(d_str, dim=True)
            typer.echo(f"  {x_vals[r]:>6}  {d_str}")
        else:
            prefix = f"  {y_vals[r]:>6}" if r < len(y_vals) else "        "
            line = ""
            for c in range(min(cols, 16)):
                d = diffs[r * cols + c]
                d_str = f"{d:>+{cell_w}}"
                if d != 0:
                    d_str = typer.style(d_str, fg=typer.colors.YELLOW, bold=True)
                else:
                    d_str = typer.style(d_str, dim=True)
                line += d_str
            if cols > 16:
                line += f"  … +{cols - 16}"
            typer.echo(f"{prefix}{line}")

    if rows > 20:
        typer.echo(typer.style(f"  … +{rows - 20} rows", dim=True))


def _print_unmatched_map(data: bytes, t: MapTable, label: str) -> None:
    """Print a compact one-line summary of an unmatched map in verbose mode."""
    x_vals = _read_axis_values(data, t.x_axis_offset, t.cols, t.byte_order)
    y_vals: tuple[int, ...] = ()
    if t.y_axis_offset is not None and t.rows > 1:
        y_vals = _read_axis_values(data, t.y_axis_offset, t.rows, t.byte_order)

    x_preview = ", ".join(str(v) for v in x_vals[:6])
    if len(x_vals) > 6:
        x_preview += f"  … +{len(x_vals) - 6}"
    y_preview = ", ".join(str(v) for v in y_vals[:4]) if y_vals else "—"
    if len(y_vals) > 4:
        y_preview += f"  … +{len(y_vals) - 4}"

    typer.echo(
        f"    {label}  0x{t.offset:08X}  {t.cols}×{t.rows}  "
        f"u{t.cell_width * 8} {_bo_label(t.byte_order)}  "
        f"X=[{x_preview}]  Y=[{y_preview}]",
    )


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def _export_markdown(
    stock_name: str,
    tuned_name: str,
    matches: list[dict],
    unmatched_stock: list[MapTable],
    unmatched_tuned: list[MapTable],
    stock_data: bytes,
    tuned_data: bytes,
    path: Path,
    unidentified: list[dict] | None = None,
) -> None:
    """Write a single self-contained Markdown report.

    Renders natively on GitHub, in VS Code, or any Markdown viewer.
    Changed cells are **bold** in diff grids.
    """
    lines: list[str] = []

    lines.append(f"# Map Diff: `{stock_name}` vs `{tuned_name}`")
    lines.append("")
    lines.append(
        f"{len(matches):,} matched  •  "
        f"{len(unmatched_stock)} only-in-stock  •  "
        f"{len(unmatched_tuned)} only-in-tuned"
    )
    lines.append("")

    # ── Summary table ──────────────────────────────────────────────
    changed_maps = [m for m in matches if m["changed_cells"] > 0]
    if changed_maps:
        lines.append("## Changed Maps")
        lines.append("")
        lines.append(
            "| Offset | Dim | Max Δ | Avg Δ | Max % | Avg % | Changed |\n"
            "|--------|-----|-------|-------|-------|-------|---------|"
        )
        for m in changed_maps:
            lines.append(
                f"| `0x{m['offset_stock']:08X}` "
                f"| {m['cols']}×{m['rows']} "
                f"| {m['max_abs']:.1f} "
                f"| {m['avg_abs']:.1f} "
                f"| {_pct_str(m['max_pct']).strip()} "
                f"| {_pct_str(m['avg_pct']).strip()} "
                f"| {m['changed_cells']}/{m['total_cells']} |"
            )
        lines.append("")

    # ── Per-map grids ──────────────────────────────────────────────
    for m in changed_maps:
        cols = m["cols"]
        rows = m["rows"]
        bo = m["byte_order"]
        cw = m["cell_width"]
        is_1d = rows == 1
        le = bo == "little"

        st_ref = m.get("_stock_table")
        if st_ref is None:
            continue

        x_vals = _read_axis_values(stock_data, st_ref.x_axis_offset, cols, bo)
        y_vals: tuple[int, ...] = ()
        if not is_1d and st_ref.y_axis_offset is not None:
            y_vals = _read_axis_values(stock_data, st_ref.y_axis_offset, rows, bo)

        sc = _read_cells(
            stock_data, m["offset_stock"], cols, rows, cw, bo, m.get("stride"),
        )
        tc = _read_cells(
            tuned_data, m["offset_tuned"], cols, rows, cw, bo, m.get("stride"),
        )

        cell_w = "u8" if cw == 1 else "u16"

        lines.append(
            f"## `0x{m['offset_stock']:08X}` — {cols}×{rows} {cell_w} "
            f"{_bo_label(bo)}  "
            f"(max Δ {m['max_abs']:.1f}, "
            f"{_pct_str(m['max_pct']).strip()})"
        )
        if m.get("suspicious"):
            lines.append("")
            lines.append(
                "> ⚠ **Suspicious** — near-total cell change with weak "
                "correlation.  The grids may be misaligned (different maps "
                "sharing the same axes)."
            )
        lines.append("")

        if is_1d:
            lines.append("| X | Stock | Tuned |\n"
                         "|---|-------|-------|")
            for i in range(cols):
                t_val = str(tc[i])
                if tc[i] != sc[i]:
                    t_val = f"`{tc[i]}`"
                lines.append(
                    f"| {x_vals[i]} | {sc[i]} | {t_val} |"
                )
        else:
            header = "| | " + " | ".join(str(x) for x in x_vals) + " |"
            sep = "|---" * (cols + 1) + "|"

            lines.append("### Stock")
            lines.append("")
            lines.append(header)
            lines.append(sep)
            for r in range(rows):
                row = " | ".join(
                    [str(y_vals[r])]
                    + [str(sc[r * cols + c]) for c in range(cols)]
                )
                lines.append(f"| {row} |")
            lines.append("")

            lines.append("### Tuned  —  `code` = changed")
            lines.append("")
            lines.append(header)
            lines.append(sep)
            for r in range(rows):
                cells = []
                for c in range(cols):
                    val = tc[r * cols + c]
                    if val != sc[r * cols + c]:
                        cells.append(f"`{val}`")
                    else:
                        cells.append(str(val))
                row = " | ".join([str(y_vals[r])] + cells)
                lines.append(f"| {row} |")
        lines.append("")

    # ── Unmatched ──────────────────────────────────────────────────
    if unmatched_stock or unmatched_tuned:
        lines.append("## Unmatched")
        lines.append("")
        if unmatched_stock:
            lines.append(f"**Only in stock ({len(unmatched_stock)}):**  ")
            lines.append(
                ", ".join(
                    f"`0x{t.offset:08X}` ({t.cols}×{t.rows})"
                    for t in unmatched_stock
                )
            )
            lines.append("")
        if unmatched_tuned:
            lines.append(f"**Only in tuned ({len(unmatched_tuned)}):**  ")
            lines.append(
                ", ".join(
                    f"`0x{t.offset:08X}` ({t.cols}×{t.rows})"
                    for t in unmatched_tuned
                )
            )
            lines.append("")

    # ── Changed but not identified ─────────────────────────────────
    if unidentified:
        lines.append("## Changed but not identified")
        lines.append("")
        lines.append(
            f"{len(unidentified)} changed region(s) not recognised as "
            f"calibration tables:"
        )
        lines.append("")
        lines.append(
            ", ".join(
                f"`0x{r['offset']:08X}` ({r['size']} B)"
                for r in unidentified[:50]
            )
        )
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def _json_safe(m: dict) -> dict:
    """Replace inf/-inf/nan values so the dict is JSON-serialisable."""
    clean = dict(m)
    for key in ("max_pct", "avg_pct"):
        val = clean.get(key)
        if isinstance(val, float) and val == float("inf"):
            clean[key] = "inf"
        elif isinstance(val, float) and val == float("-inf"):
            clean[key] = "-inf"
    return clean


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def diff_maps(
    stock: Path = typer.Argument(
        ...,
        help="Stock/original ECU binary (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    tuned: Path = typer.Argument(
        ...,
        help="Tuned/modified ECU binary (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    min_score: float = typer.Option(
        0.55,
        "--min-score",
        "-s",
        help="Minimum table score in [0, 1] (default: 0.55, lower than scan-maps to avoid missing changed maps).",
    ),
    threshold: float = typer.Option(
        0.0,
        "--threshold",
        "-t",
        help="Only show maps with max absolute cell change >= threshold.",
    ),
    top: int = typer.Option(
        50,
        "--top",
        "-n",
        help="Max matched maps to show (default: 50).",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        help="Group output: show only the top-3 changed maps per group.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show before/after cell grids for each changed map.",
    ),
    export: Path | None = typer.Option(
        None,
        "--export",
        help="Export the diff report as Markdown (diff.md) to a directory. Includes changed-map grids with changed values highlighted.",
        exists=False,
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    region: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="Restrict scanning to a byte range: '0xSTART-0xEND' (hex values, 0x optional). Overrides the calibration-region default.",
        metavar="RANGE",
    ),
    whole_file: bool = typer.Option(
        False,
        "--whole-file",
        help="Scan the whole file instead of only the detected calibration region (shows tables outside it).",
    ),
    max_series_tables: int = typer.Option(
        16,
        "--max-series-tables",
        help="Max consecutive shared-axis tables to probe (1=off, default: 16).",
    ),
    recipe: Path | None = typer.Option(
        None,
        "--recipe",
        help="Cross-reference a .remap recipe: mark which cells each recipe instruction covers and report changed cells NOT in the recipe (untracked changes).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    annotate: Path | None = typer.Option(
        None,
        "--annotate",
        help="With --recipe: write the recipe augmented with a schema 4.4 'maps' layer (map descriptors + instruction refs) to this path.",
        exists=False,
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
    ),
) -> None:
    """
    Diff calibration maps between a stock and tuned ECU binary.

    Scans both files for calibration tables, matches them by axis
    fingerprint (X and Y axis value tuples), and reports cell-by-cell
    changes for each matched pair.

    Maps whose axis breakpoints changed between stock and tuned will
    appear as unmatched rather than matched — this is intentional.
    Tuners rarely change breakpoints; when they do, the map structure
    itself has changed.

    Useful for auditing a tune: which maps were modified, how aggressive
    each change is, and whether anything looks suspicious.
    """
    # ── Read binaries ────────────────────────────────────────────────
    stock_data = _read_bin(stock, "Stock")
    tuned_data = _read_bin(tuned, "Tuned")
    region_slice = _parse_region(region)
    changed_blocks = find_changed_blocks(stock_data, tuned_data, 16)

    # ── Scan both files ──────────────────────────────────────────────
    t0 = time.perf_counter()

    stock_result = _scan_one(
        stock_data, region_slice, min_score, max_series_tables,
        layout_default=not whole_file,
    )
    tuned_result = _scan_one(
        tuned_data, region_slice, min_score, max_series_tables,
        layout_default=not whole_file,
    )

    stock_tables: list[MapTable] = stock_result["tables"]
    tuned_tables: list[MapTable] = tuned_result["tables"]

    elapsed_scan = time.perf_counter() - t0

    # ── Build stock fingerprint index ────────────────────────────────
    stock_index = _build_stock_index(stock_data, stock_tables)

    # ── Match & diff ─────────────────────────────────────────────────
    # One-to-one greedy: each stock table matched at most once.
    # When multiple stock tables share an axis fingerprint (common —
    # many maps use the same RPM breakpoints), pick the closest by
    # offset.  When only one candidate remains, take it directly.
    matches: list[dict] = []
    unmatched_tuned: list[MapTable] = []
    used_stock_offsets: set[int] = set()

    for tt in tuned_tables:
        fp = _axis_fingerprint(tuned_data, tt)
        candidates = stock_index.get(fp, [])

        # Filter out already-matched stock tables
        available = [
            st for st in candidates if st.offset not in used_stock_offsets
        ]

        if not available:
            unmatched_tuned.append(tt)
            continue

        # Pick closest by offset
        best = min(available, key=lambda st: abs(st.offset - tt.offset))
        used_stock_offsets.add(best.offset)

        # The scanner guesses the data offset via axis pairing, and its
        # padding choice can differ between two nearly-identical binaries
        # (cell values influence the pairing heuristics).  Search the pad
        # variants (±4 bytes, the scanner's PADDING_OFFSETS range) around
        # both guessed offsets and keep the alignment with the fewest
        # changed cells — a real tune changes a minority of cells, so the
        # correct alignment minimises the diff.
        # The scanner guesses each table's data offset via axis pairing; its
        # padding choice can differ between two nearly-identical binaries.
        # For compound (strided) tables the Rust split pass already pinned
        # the data offset structurally (immediately after the shared Y
        # axis), so the offsets are trusted directly.  For contiguous
        # tables, search pad variants for the alignment with the fewest
        # changed cells — a real tune changes a minority of cells.
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
        # After the best alignment, near-total cell change means the grids
        # still don't line up — probably different maps that share axes.
        # Correlation tells the two cases apart: a heavily retuned map
        # correlates strongly with its stock grid (same map, new values),
        # while a different map sharing the axes looks unrelated.
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

    # ── Near-match pass: tables whose axis breakpoints changed ─────
    # Exact fingerprint matching is deliberately strict; a tuner editing
    # a map's axis breakpoints would otherwise silently drop it into the
    # only-in-* lists.  Correlation-based near-matching pairs those up.
    unmatched_tuned, near_matches = _near_match_pass(
        stock_data, tuned_data,
        stock_tables, used_stock_offsets, unmatched_tuned,
    )
    matches.extend(near_matches)

    # Unmatched in stock: tables whose offset was never consumed
    unmatched_stock = [
        t
        for t in stock_tables
        if t.offset not in used_stock_offsets
    ]

    # Promote changed blocks the axis scanner cannot see (flat-Y tables,
    # constant maps, missing breakpoints) into synthetic matches.
    matches.extend(
        _promote_uncovered_changed_blocks(
            stock_data, tuned_data, changed_blocks, matches,
        )
    )

    # Changed blocks no matched table covers — the "changed but not
    # identified" audit gap: bytes that differ but were not recognised
    # as calibration tables.  Every changed byte in the binary is now
    # accounted for: matched map, promoted table, or listed here.
    unidentified = _unidentified_changed_blocks(changed_blocks, matches)

    # Sort matches by max_abs descending (inf sorts last via key)
    def _sort_key(m: dict) -> float:
        val = m["max_abs"]
        if isinstance(val, float) and val == float("inf"):
            return float("inf")
        return float(val)

    matches.sort(key=_sort_key, reverse=True)

    # ── Recipe cross-reference (optional) ─────────────────────────────
    recipe_data: dict | None = None
    recipe_summary: dict = {}
    if recipe is not None:
        recipe_data = _load_recipe(recipe)
        recipe_summary = _annotate_matches(
            matches, stock_data, tuned_data, recipe_data,
        )

        if annotate is not None:
            from openremap.core.services.recipes.recipe_maps import attach_maps

            attach_maps(recipe_data, stock_data)
            annotate.parent.mkdir(parents=True, exist_ok=True)
            annotate.write_text(
                json.dumps(recipe_data, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            typer.echo(
                typer.style(
                    f"\n  Annotated recipe written to {annotate} "
                    f"({len(recipe_data['maps'])} map(s), schema 4.4)",
                    fg=typer.colors.GREEN,
                ),
                err=True,
            )

    # Apply threshold
    above_threshold = [
        m for m in matches
        if (
            isinstance(m["max_abs"], float) and m["max_abs"] == float("inf")
        ) or m["max_abs"] >= threshold
    ]
    shown = above_threshold[:top]

    # ── JSON output ──────────────────────────────────────────────────
    if as_json:
        # Group shown matches by axis fingerprint for structured consumers.
        from collections import defaultdict as _defaultdict_json

        jgroups: dict[tuple, list[dict]] = _defaultdict_json(list)
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

        # Strip internal MapTable refs (not JSON-serialisable)
        json_matches = []
        for m in shown:
            clean = dict(m)
            clean.pop("_stock_table", None)
            clean.pop("_tuned_table", None)
            clean.pop("_fp", None)
            clean["group"] = group_index[id(m)]
            json_matches.append(_json_safe(clean))

        if recipe_summary:
            recipe_summary["file"] = str(recipe)
        out = {
            "stock": str(stock),
            "tuned": str(tuned),
            "recipe": recipe_summary or None,
            "stock_size": len(stock_data),
            "tuned_size": len(tuned_data),
            "stock_tables": len(stock_tables),
            "tuned_tables": len(tuned_tables),
            "stock_tables_hidden": stock_result.get("tables_hidden", 0),
            "tuned_tables_hidden": tuned_result.get("tables_hidden", 0),
            "matched_count": len(matches),
            "above_threshold": len(above_threshold),
            "only_in_stock_count": len(unmatched_stock),
            "only_in_tuned_count": len(unmatched_tuned),
            "unidentified_changed_count": len(unidentified),
            "unidentified_changed": unidentified[:200],
            "scan_seconds": round(elapsed_scan, 2),
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
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        return

    # ── Human-readable output ────────────────────────────────────────
    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Map-Level Diff", bold=True))
    typer.echo(
        typer.style(
            f"  {stock.name}  vs  {tuned.name}  •  "
            f"{len(matches):,} matched  •  "
            f"{len(unmatched_stock)} only-in-stock  •  "
            f"{len(unmatched_tuned)} only-in-tuned  •  "
            f"{len(unidentified)} unidentified  •  "
            f"scan {elapsed_scan:.1f}s",
            dim=True,
        ),
    )
    hidden_total = (
        stock_result.get("tables_hidden", 0)
        + tuned_result.get("tables_hidden", 0)
    )
    if hidden_total:
        typer.echo(
            typer.style(
                f"  ({hidden_total} table(s) outside the calibration region "
                f"hidden — use --whole-file to scan the whole file)",
                dim=True,
            ),
        )
    typer.echo("")

    # Heuristic: if very few maps matched, the files may be unrelated
    smaller_total = min(len(stock_tables), len(tuned_tables))
    match_pct = (len(matches) / smaller_total * 100) if smaller_total else 0

    if match_pct < 5:
        typer.echo(
            typer.style(
                f"  ⚠  Only {len(matches)} maps matched ({match_pct:.1f}% of "
                f"{smaller_total} tables in the smaller file).  "
                f"These files may not be from the same ECU, or one may not "
                f"be a tune of the other.",
                fg=typer.colors.YELLOW,
            ),
        )
        typer.echo("")

    if not matches:
        typer.echo(
            typer.style(
                "  No matching maps found between the two files.  "
                "The tuned file may be from a different ECU or the axis "
                "breakpoints were changed.",
                fg=typer.colors.YELLOW,
            ),
        )
        typer.echo("")
        return

    if recipe_summary:
        covered = recipe_summary["covered_changed_cells"]
        untracked = recipe_summary["untracked_changed_cells"]
        typer.echo(
            typer.style(
                f"  ◆ Recipe cross-reference ({recipe.name})\n"
                f"    {recipe_summary['instructions']} instruction(s) touch "
                f"{recipe_summary['maps_touched']} map(s) — "
                f"{covered} of the changed cells are covered.\n"
                f"    Untracked: {untracked} changed cell(s) not present "
                f"in the recipe.",
                fg=typer.colors.CYAN,
            ),
        )
        typer.echo("")

    # ── Group matched maps by axis fingerprint ─────────────────────────
    # Maps sharing identical X/Y axis breakpoints form a group (the same
    # RPM×Load axes often drive fuel, timing, and boost maps together).
    from collections import defaultdict as _defaultdict

    grouped: dict[tuple, list[dict]] = _defaultdict(list)
    for m in shown:
        grouped[m["_fp"]].append(m)
    # Order groups by their most-changed member (descending).
    group_items = sorted(
        grouped.items(),
        key=lambda kv: _sort_key(kv[1][0]),
        reverse=True,
    )

    # ── Matched table ────────────────────────────────────────────────
    hdr = typer.style(
        f"  {'Offset':>10}  {'Dim':>8}  {'Cells':>7}  "
        f"{'Max Δ':>8}  {'Avg Δ':>8}  {'Max %':>8}  {'Avg %':>8}  "
        f"{'Changed':>8}",
        bold=True,
    )
    typer.echo(hdr)
    typer.echo(typer.style("  " + "─" * 80, dim=True))

    def _row_text(m: dict) -> str:
        dim = f"{m['cols']}×{m['rows']}"
        cells = (
            f"{'u8' if m['cell_width'] == 1 else 'u16'} "
            f"{_bo_label(m['byte_order'])}"
        )
        changed = f"{m['changed_cells']}/{m['total_cells']}"

        # Colour the max_abs cell
        if isinstance(m["max_abs"], float) and m["max_abs"] == float("inf"):
            max_colour = typer.colors.RED
            max_display = "     ∞"
        else:
            if m["max_abs"] >= 20:
                max_colour = typer.colors.RED
            elif m["max_abs"] >= 5:
                max_colour = typer.colors.YELLOW
            else:
                max_colour = typer.colors.GREEN
            max_display = f"{m['max_abs']:8.2f}"

        marker = ""
        if m.get("recipe_cells_covered"):
            covered = m["recipe_cells_covered"]
            changed = m.get("changed_cells", 0)
            marker += typer.style(
                f"  ◆ recipe {covered}/{changed}",
                fg=typer.colors.CYAN,
            )
        if m.get("near_match"):
            marker += typer.style(
                "  ↺ axes changed (correlation near-match)",
                fg=typer.colors.YELLOW,
            )
        elif m.get("promoted"):
            marker += typer.style(
                "  ⚑ no-axis table (detected from changed bytes)",
                fg=typer.colors.YELLOW,
                dim=True,
            )
        elif m.get("suspicious"):
            marker += typer.style(
                "  ⚠ suspicious (grids don't line up — different map?)",
                fg=typer.colors.RED,
                bold=True,
            )
        elif m.get("realigned"):
            marker += typer.style("  ↻ realigned", fg=typer.colors.YELLOW, dim=True)
        corr = m.get("correlation")
        if m.get("suspicious") and corr is not None:
            marker += typer.style(
                f" · r={corr:.2f}", fg=typer.colors.RED, dim=True,
            )

        return (
            f"  0x{m['offset_stock']:08X}  {dim:>8}  {cells:>7}  "
            + typer.style(max_display, fg=max_colour)
            + f"  {m['avg_abs']:8.2f}  "
            + f"{_pct_str(m['max_pct'])}  {_pct_str(m['avg_pct'])}  "
            + f"{changed:>8}"
            + marker
        )

    for gi, (_fp, members) in enumerate(group_items):
        anchor = members[0]
        x_vals, y_vals = _fp
        if not x_vals and not y_vals:
            group_label = (
                f"Group {_group_id(gi)} — {len(members)} changed-block table(s) · "
                f"no axes (detected from changed bytes)"
            )
        else:
            group_label = (
                f"Group {_group_id(gi)} — {len(members)} map(s) · "
                f"{anchor['cols']}×{anchor['rows']} "
                f"{'u8' if anchor['cell_width'] == 1 else 'u16'} "
                f"{_bo_label(anchor['byte_order'])} · "
                f"X=[{_axis_preview(x_vals)}]"
                + (f"  Y=[{_axis_preview(y_vals)}]" if y_vals else "")
            )
        typer.echo(typer.style(f"  {group_label}", fg=typer.colors.CYAN, bold=True))

        visible = members[:3] if compact else members
        for m in visible:
            typer.echo(_row_text(m))
        if compact and len(members) > len(visible):
            typer.echo(
                typer.style(
                    f"      … and {len(members) - len(visible)} more in this group",
                    dim=True,
                )
            )
        typer.echo("")

    # ── Markdown export ────────────────────────────────────────────────
    if export is not None:
        export.mkdir(parents=True, exist_ok=True)

        _export_markdown(
            stock.name, tuned.name,
            matches, unmatched_stock, unmatched_tuned,
            stock_data, tuned_data,
            export / "diff.md",
            unidentified,
        )

        typer.echo(
            typer.style(
                "  Exported: diff.md",
                fg=typer.colors.GREEN,
            ),
        )
        typer.echo(f"  {export}/")
        typer.echo("")

    # ── Verbose: before/after cell grids ────────────────────────────
    if verbose:
        for m in shown:
            if "_stock_table" in m:
                _print_map_grids(
                    stock_data, tuned_data, m, m.pop("_stock_table"),
                )

    # Clean up internal refs from all match dicts
    for m in matches:
        m.pop("_stock_table", None)
        m.pop("_tuned_table", None)
        m.pop("_fp", None)

    if len(matches) > len(shown):
        remaining = len(matches) - len(shown)
        typer.echo(
            typer.style(
                f"  … and {remaining} more matched maps.  "
                f"Use --top {min(len(matches), top + remaining)} to see all, "
                f"or --threshold to filter.",
                dim=True,
            ),
        )
        typer.echo("")

    if threshold > 0 and len(above_threshold) < len(matches):
        hidden = len(matches) - len(above_threshold)
        typer.echo(
            typer.style(
                f"  {hidden} map(s) hidden by --threshold {threshold}.",
                dim=True,
            ),
        )
        typer.echo("")

    # ── Unmatched section ────────────────────────────────────────────
    if unmatched_stock or unmatched_tuned:
        typer.echo(
            typer.style(
                "  ── Unmatched ─────────────────────────────────────────────",
                bold=True,
            ),
        )
        typer.echo("")

        if unmatched_stock:
            label = typer.style(
                f"  Only in stock ({len(unmatched_stock)}):",
                fg=typer.colors.CYAN,
            )
            preview = ", ".join(
                f"0x{t.offset:08X} ({_dim_str(t)})"
                for t in unmatched_stock[:8]
            )
            if len(unmatched_stock) > 8:
                preview += f"  … +{len(unmatched_stock) - 8} more"
            typer.echo(f"{label}  {preview}")
            typer.echo("")

            if verbose:
                for t in unmatched_stock[:30]:
                    _print_unmatched_map(stock_data, t, "stock")
                if len(unmatched_stock) > 30:
                    typer.echo(
                        typer.style(
                            f"    … +{len(unmatched_stock) - 30} more",
                            dim=True,
                        ),
                    )
                typer.echo("")

        if unmatched_tuned:
            label = typer.style(
                f"  Only in tuned ({len(unmatched_tuned)}):",
                fg=typer.colors.CYAN,
            )
            preview = ", ".join(
                f"0x{t.offset:08X} ({_dim_str(t)})"
                for t in unmatched_tuned[:8]
            )
            if len(unmatched_tuned) > 8:
                preview += f"  … +{len(unmatched_tuned) - 8} more"
            typer.echo(f"{label}  {preview}")
            typer.echo("")

            if verbose:
                for t in unmatched_tuned[:30]:
                    _print_unmatched_map(tuned_data, t, "tuned")
                if len(unmatched_tuned) > 30:
                    typer.echo(
                        typer.style(
                            f"    … +{len(unmatched_tuned) - 30} more",
                            dim=True,
                        ),
                    )
                typer.echo("")

    # ── Changed but not identified ─────────────────────────────────
    if unidentified:
        typer.echo(
            typer.style(
                "  ── Changed but not identified ────────────────────────────",
                bold=True,
            ),
        )
        typer.echo("")
        label = typer.style(
            f"  {len(unidentified)} changed region(s) not recognised as "
            f"calibration tables:",
            fg=typer.colors.YELLOW,
        )
        preview = ", ".join(
            f"0x{r['offset']:08X} ({r['size']} B)"
            for r in unidentified[:8]
        )
        if len(unidentified) > 8:
            preview += f"  … +{len(unidentified) - 8} more"
        typer.echo(f"{label}  {preview}")
        typer.echo("")

    typer.echo("")
