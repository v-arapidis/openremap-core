"""
Generic calibration map axis scanner — the 'bullshit detector'.

Scans ECU binaries for monotonically increasing 16-bit axis sequences that
indicate genuine calibration map structures.  Used as a confidence signal:
if an extractor identifies a binary as a modern ECU but zero map axes are
found, the file may be encrypted, corrupted, or misidentified.

ECU calibration maps store data in 2D tables with monotonically increasing
axes (e.g. RPM breakpoints, load breakpoints).  The axes are typically
stored as sequences of 16-bit unsigned integers in either little-endian or
big-endian byte order.

The scanner is intentionally conservative — it looks for *plausible* axes
rather than trying to parse any specific map format.
"""

from __future__ import annotations

import struct
import time
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


class MapAxis(NamedTuple):
    """A single plausible calibration map axis found in the binary."""

    offset: int
    """Byte offset (within the scanned region) where the axis starts."""

    length: int
    """Number of 16-bit values in the axis."""

    byte_order: str
    """Either ``'little'`` or ``'big'``."""

    values: tuple[int, ...]
    """The decoded 16-bit values forming the axis."""


class MapTable(NamedTuple):
    """A plausible 2D calibration table located by axis pairing.

    A table is a rectangular block of ``cols * rows`` 16-bit values that
    follows a pair of monotonically-increasing axes (X then Y) in the
    binary.  Layout assumed:

    ``[ X axis (cols * 2 B) | Y axis (rows * 2 B) | data (cols * rows * 2 B) ]``

    This covers the single most common WinOLS / DAMOS table layout.
    1D tables (one axis followed by a data vector) are reported as
    tables with ``rows == 1`` and ``y_axis_offset is None``.
    """

    offset: int
    """Byte offset where the data block starts (after the axes)."""

    cols: int
    """Number of columns — equals length of the X axis."""

    rows: int
    """Number of rows — equals length of the Y axis (1 for vector tables)."""

    cell_width: int
    """Bytes per cell (currently always 2 — u16)."""

    byte_order: str
    """Either ``'little'`` or ``'big'`` — matches the axes."""

    x_axis_offset: int
    """Byte offset of the X axis (cols values)."""

    y_axis_offset: int | None
    """Byte offset of the Y axis, or ``None`` for 1D / vector tables."""

    score: float
    """Heuristic confidence in ``[0.0, 1.0]`` — higher is better."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# struct format characters for 16-bit unsigned, by byte order.
_FMT: dict[str, str] = {"little": "<H", "big": ">H"}

# Minimum number of consecutive bytes that must be non-trivial (not all
# 0x00 or all 0xFF) before we bother decoding.  Set to 8 so that we need
# at least 4 × u16 values worth of non-trivial data.
_SKIP_WINDOW = 8


def _is_trivial_block(data: bytes, start: int, length: int) -> bool:
    """Return *True* if the *length*-byte block at *start* is all-zero or
    all-0xFF.  Used to skip erased flash / empty regions quickly."""
    end = min(start + length, len(data))
    if end <= start:
        return True
    block = data[start:end]
    first = block[0]
    if first not in (0x00, 0xFF):
        return False
    # Fast path: compare against a single-byte fill.
    return block == bytes([first]) * len(block)


def _try_axis_at(
    data: bytes,
    offset: int,
    fmt: str,
    min_axis_length: int,
    max_axis_length: int,
    min_step: int,
    max_step: int,
) -> int:
    """Starting at *offset*, try to read the longest strictly-increasing
    run of 16-bit values using *fmt* (a ``struct`` format character).

    Returns the number of consecutive increasing values found (≥ 1), or 0
    if even the first value cannot be read.
    """
    data_len = len(data)
    if offset + 2 > data_len:
        return 0

    count = 1
    prev: int = struct.unpack_from(fmt, data, offset)[0]
    pos = offset + 2
    limit = min(offset + max_axis_length * 2, data_len)

    while pos + 1 < limit:
        cur: int = struct.unpack_from(fmt, data, pos)[0]
        diff = cur - prev
        if diff < min_step or diff > max_step:
            break
        prev = cur
        pos += 2
        count += 1

    return count


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------


def scan_map_axes(
    data: bytes,
    region: slice | None = None,
    min_axis_length: int = 4,
    max_axis_length: int = 32,
    min_step: int = 1,
    max_step: int = 10000,
) -> list[MapAxis]:
    """Scan *data* for plausible 16-bit calibration map axes.

    Parameters
    ----------
    data:
        Raw ECU binary content.
    region:
        Optional ``slice`` to restrict scanning to a sub-region of *data*.
        When *None*, the entire buffer is scanned.
    min_axis_length:
        Minimum number of consecutive strictly-increasing 16-bit values
        required to consider a run a plausible axis (default **4**, i.e.
        8 bytes).
    max_axis_length:
        Maximum axis length to consider (default **32**).  Axes longer than
        this are unlikely in real ECU calibrations and may indicate
        coincidental data.
    min_step:
        Minimum allowed difference between consecutive axis values
        (default **1**).  A step of 0 would mean duplicate values.
    max_step:
        Maximum allowed difference between consecutive axis values
        (default **10 000**).  Very large jumps are unlikely in real
        breakpoint tables.

    Returns
    -------
    list[MapAxis]
        All plausible axes found, deduplicated across byte orders.  Each
        axis is reported only once even if both endianness interpretations
        would qualify (the first match wins, little-endian is tried first).
    """
    if region is not None:
        buf = data[region]
        # Work on the sliced copy; offsets are relative to the region.
    else:
        buf = data

    buf_len = len(buf)
    if buf_len < min_axis_length * 2:
        return []

    # We track which byte offsets have already been claimed by a found axis
    # so that overlapping / duplicate detections across byte orders are
    # suppressed.
    claimed_offsets: set[int] = set()
    results: list[MapAxis] = []

    # Try little-endian first, then big-endian.
    for byte_order in ("little", "big"):
        fmt = _FMT[byte_order]
        offset = 0

        while offset + min_axis_length * 2 <= buf_len:
            # --- fast skip: trivial (all-zero / all-0xFF) regions ---------
            if _is_trivial_block(buf, offset, _SKIP_WINDOW):
                # Jump forward in larger strides to leave the trivial zone.
                offset += _SKIP_WINDOW
                continue

            # --- check if this offset is already claimed ------------------
            if offset in claimed_offsets:
                offset += 2
                continue

            # --- attempt to read an axis ----------------------------------
            run_len = _try_axis_at(
                buf,
                offset,
                fmt,
                min_axis_length,
                max_axis_length,
                min_step,
                max_step,
            )

            if run_len >= min_axis_length:
                # Decode the full axis values for the result.
                values = tuple(
                    struct.unpack_from(fmt, buf, offset + i * 2)[0]
                    for i in range(run_len)
                )
                axis = MapAxis(
                    offset=offset,
                    length=run_len,
                    byte_order=byte_order,
                    values=values,
                )
                results.append(axis)

                # Claim every byte offset covered by this axis so the
                # other-endianness pass won't double-count it.
                for i in range(run_len):
                    claimed_offsets.add(offset + i * 2)

                # Skip past the axis before continuing.
                offset += run_len * 2
            else:
                offset += 2

    # Collapse contained sub-axes: a shorter axis whose entire byte
    # range lies inside a longer axis of the same byte order is almost
    # always a redundant detection (the longer axis is the canonical
    # one).  This both shrinks the reported list and cuts table-pairing
    # work proportionally.
    if len(results) > 1:
        by_order_runs: dict[str, list[tuple[int, int, int]]] = {}
        for idx, ax in enumerate(results):
            by_order_runs.setdefault(ax.byte_order, []).append(
                (ax.offset, ax.offset + ax.length * 2, idx)
            )
        drop: set[int] = set()
        for runs in by_order_runs.values():
            runs.sort(key=lambda r: (r[0], -(r[1] - r[0])))
            # Sweep: keep a stack of currently-open longest ranges.
            for i, (s_i, e_i, idx_i) in enumerate(runs):
                for j in range(i):
                    s_j, e_j, _ = runs[j]
                    if e_j <= s_i:
                        continue
                    if s_j <= s_i and e_i <= e_j and (e_i - s_i) < (e_j - s_j):
                        drop.add(idx_i)
                        break
        if drop:
            results = [ax for i, ax in enumerate(results) if i not in drop]

    return results


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def count_map_axes(
    data: bytes,
    region: slice | None = None,
    min_axis_length: int = 4,
    max_axis_length: int = 32,
    min_step: int = 1,
    max_step: int = 10000,
) -> int:
    """Return the number of plausible calibration map axes in *data*.

    This is a thin convenience wrapper around :func:`scan_map_axes` — see
    that function's docstring for parameter details.
    """
    return len(
        scan_map_axes(
            data,
            region=region,
            min_axis_length=min_axis_length,
            max_axis_length=max_axis_length,
            min_step=min_step,
            max_step=max_step,
        )
    )


# ---------------------------------------------------------------------------
# Table detection (axis pairing)
# ---------------------------------------------------------------------------
#
# WinOLS-style "potential maps" are rectangular blocks of cells (u8 or
# u16) that follow a pair of monotonically-increasing axes.  We promote
# pairs of axes found by `scan_map_axes` into `MapTable` candidates by
# looking at the bytes *immediately after* the second axis and checking
# they form a plausible rectangle of values.
#
# Goals — match or exceed WinOLS' "potential maps":
#   * Multiple cell widths (1, 2 bytes).
#   * Tolerate small alignment padding (2 / 4 / 8 bytes) between the
#     axes and the data block — modern compilers align table data.
#   * Recover from greedy axis scanning by truncating either X or Y up
#     to ``max_gap`` and re-scanning for the missing axis.
#   * Rich scoring: row + column smoothness, 2D Laplacian consistency,
#     monotonic-direction bonus, distinct-value floor, stripe / repeat
#     penalty, sentinel and trivial-value penalties, axis-quality bonus.
#   * Anti-text guard: regions of dense printable ASCII are rejected.
#
# The detector is intentionally conservative.


# Minimum total cells in a candidate table.
_MIN_TABLE_CELLS = 8

# Bounded-value caps per cell width (excluded values still scored).
_TABLE_MAX_VALUE_U16 = 0xF000
_TABLE_MAX_VALUE_U8 = 0xF0

# Known ECU fill / clamp / sentinel patterns per cell width.  0x7FFF /
# 0x7F appears in signed-clamp regions.
_SENTINELS_U16 = frozenset({0x0000, 0xFFFF, 0x8000, 0x7FFF})
_SENTINELS_U8 = frozenset({0x00, 0xFF, 0x80, 0x7F})

# Hard erasure (flash-erase) bytes.
_ERASURES_U16 = frozenset({0x0000, 0xFFFF})
_ERASURES_U8 = frozenset({0x00, 0xFF})

# Rejection thresholds.
_TABLE_TRIVIAL_FRACTION = 0.30
_TABLE_SENTINEL_FRACTION = 0.45
_TABLE_MIN_DISTINCT_RATIO = 0.18
_TABLE_ASCII_FRACTION = 0.75

# Padding offsets to try between the end of the Y axis and the start of
# the data block.  Covers compiler-emitted natural-word alignment.
# Kept short for performance — 0/2/4 covers ~all observed ECU layouts.
_PADDING_OFFSETS = (0, 2, 4)

# Maximum number of Y-length truncations tried per (X, Y) pair.  The
# greedy axis scanner can absorb the real Y axis into a longer fake
# axis, so we sweep every length down to ``min_y_length`` — the fast
# byte-level pre-filter keeps this cheap in practice.
_MAX_Y_TRUNC = 32

# Maximum Y axis length considered when forming a 2D table candidate.
# Real ECU tables almost never exceed 32×32; the cap keeps the inner
# sweep bounded.
_MAX_Y_LEN = 32

# Common ECU axis sizes used to give a small plausibility bonus.
# Wide range — odd lengths (5, 7, 9, ...) are common too; this gives a
# light reward without strongly disfavouring any reasonable size.
_COMMON_AXIS_SIZES = frozenset(range(4, 33))


def _cell_max(cell_width: int) -> int:
    return _TABLE_MAX_VALUE_U8 if cell_width == 1 else _TABLE_MAX_VALUE_U16


def _sentinels(cell_width: int) -> frozenset[int]:
    return _SENTINELS_U8 if cell_width == 1 else _SENTINELS_U16


def _erasures(cell_width: int) -> frozenset[int]:
    return _ERASURES_U8 if cell_width == 1 else _ERASURES_U16


def _read_u16_block(
    data: bytes,
    offset: int,
    count: int,
    byte_order: str,
) -> list[int] | None:
    """Decode *count* consecutive u16 values starting at *offset*."""
    end = offset + count * 2
    if end > len(data) or offset < 0:
        return None
    fmt = ("<" if byte_order == "little" else ">") + "H" * count
    return list(struct.unpack_from(fmt, data, offset))


def _read_block(
    data: bytes,
    offset: int,
    count: int,
    byte_order: str,
    cell_width: int,
) -> list[int] | None:
    """Generic block reader supporting 1- or 2-byte unsigned cells."""
    end = offset + count * cell_width
    if end > len(data) or offset < 0:
        return None
    if cell_width == 1:
        return list(data[offset:end])
    return _read_u16_block(data, offset, count, byte_order)


def _is_ascii_dense(data: bytes, offset: int, length: int) -> bool:
    """Reject candidate regions that look like strings / message tables."""
    end = min(offset + length, len(data))
    if end <= offset:
        return False
    chunk = data[offset:end]
    printable = sum(1 for b in chunk if 0x20 <= b <= 0x7E)
    return printable / len(chunk) >= _TABLE_ASCII_FRACTION


def _axis_quality(values: tuple[int, ...] | list[int]) -> float:
    """Score the plausibility of an axis in ``[0, 1]``.

    Real ECU axes tend to be strictly monotonic (already enforced),
    close to linear (constant or smoothly varying step), and of a
    "natural" length (4/6/8/10/12/16/20/24/32).
    """
    n = len(values)
    if n < 2:
        return 0.0
    steps = [values[i + 1] - values[i] for i in range(n - 1)]
    mean_step = sum(steps) / len(steps)
    if mean_step <= 0:
        return 0.0
    spread = max(steps) - min(steps)
    linearity = max(0.0, 1.0 - spread / (mean_step * 4.0))
    size_bonus = 1.0 if n in _COMMON_AXIS_SIZES else 0.7
    return 0.6 * linearity + 0.4 * size_bonus


def _line_smoothness(line: list[int]) -> tuple[float, bool, int]:
    """Return (smoothness, is_flat, monotonic_direction) for one row/col."""
    n = len(line)
    if n < 2:
        return 0.5, False, 0
    lo = hi = line[0]
    step_sum = 0
    inc = dec = True
    prev = line[0]
    for i in range(1, n):
        v = line[i]
        if v < lo:
            lo = v
        elif v > hi:
            hi = v
        d = v - prev
        if d < 0:
            inc = False
            step_sum -= d
        else:
            if d > 0:
                dec = False
            step_sum += d
        prev = v
    if hi == lo:
        return 0.5, True, 0
    mean_step = step_sum / (n - 1)
    spread = hi - lo
    smooth = 1.0 - mean_step / spread
    if smooth < 0.0:
        smooth = 0.0
    direction = 1 if inc else -1 if dec else 0
    return smooth, False, direction


def _stripe_penalty(
    values: list[int], cols: int, distinct_count: int | None = None
) -> float:
    """Return a multiplier in ``[0, 1]`` penalising obvious repeats.

    Stricter on small tables (<= 25 cells): false positives there are
    almost always periodic / repeating constants, so we widen the
    period-detection window and lower the match-ratio thresholds.
    """
    n = len(values)
    if not cols:
        return 1.0
    rows = n // cols
    small = n <= 25
    if rows >= 2:
        first = values[:cols]
        equal = 0
        for r in range(1, rows):
            if values[r * cols : (r + 1) * cols] == first:
                equal += 1
        if equal >= rows - 1:
            return 0.0
        if equal >= rows // 2:
            return 0.3 if small else 0.4
    # Period detection — only meaningful when there's enough repetition
    # potential.  If the distinct-value count is high the search is
    # almost guaranteed to find nothing.
    if distinct_count is not None and distinct_count > n // 2:
        # Small tables: tiny distinct counts are still suspicious even
        # if technically above n/2 (e.g. 9 distinct in 16 cells).
        if not (small and distinct_count <= max(4, n // 3)):
            return 1.0
    # Wider period sweep (up to 12) so we catch the period-6 patterns
    # commonly seen in packed code constants / lookup blobs.
    max_period = min(n // 3, 12)
    hi_thr = 0.85 if small else 0.9
    mid_thr = 0.65 if small else 0.75
    for period in range(2, max_period + 1):
        if n < period * 3:
            break
        matches = 0
        limit = n - period
        for i in range(period, n):
            if values[i] == values[i - period]:
                matches += 1
        ratio = matches / limit
        if ratio > hi_thr:
            return 0.15 if small else 0.2
        if ratio > mid_thr:
            return 0.4 if small else 0.5
    return 1.0


def _score_table_block(
    values: list[int],
    cols: int,
    cell_width: int = 2,
) -> float:
    """Score how "table-like" a flat list of cell values is, in ``[0, 1]``."""
    n = len(values)
    if n == 0 or cols == 0:
        return 0.0

    cell_max = _cell_max(cell_width)
    sentinels = _sentinels(cell_width)
    signed_clamp = 0x80 if cell_width == 1 else 0x8000

    # Single pass over values — collect everything we need.
    bounded_n = 0
    non_trivial_n = 0
    distinct: set[int] = set()
    for v in values:
        if v <= cell_max and v != signed_clamp:
            bounded_n += 1
        if v not in sentinels:
            non_trivial_n += 1
        distinct.add(v)
    bounded = bounded_n / n
    non_trivial = non_trivial_n / n

    rows = n // cols

    # Row smoothness + monotonicity.
    row_smooth_total = 0.0
    row_smooth_count = 0
    row_monotonic = 0
    flat_rows = 0
    if cols >= 2:
        for r in range(rows):
            s, flat, direction = _line_smoothness(values[r * cols : (r + 1) * cols])
            row_smooth_total += s
            row_smooth_count += 1
            if flat:
                flat_rows += 1
            if direction != 0:
                row_monotonic += 1
    row_smooth = row_smooth_total / row_smooth_count if row_smooth_count else 0.5
    flat_fraction = (flat_rows / rows) if rows else 0.0
    row_smooth *= max(0.3, 1.0 - flat_fraction)

    # Column smoothness + monotonicity.
    col_smooth_total = 0.0
    col_smooth_count = 0
    col_monotonic = 0
    if rows >= 2:
        for c in range(cols):
            col = [values[r * cols + c] for r in range(rows)]
            s, _flat, direction = _line_smoothness(col)
            col_smooth_total += s
            col_smooth_count += 1
            if direction != 0:
                col_monotonic += 1
    col_smooth = col_smooth_total / col_smooth_count if col_smooth_count else 0.5

    # Monotonic-axis bonus.
    if rows >= 2 and cols >= 2:
        mono_score = max(row_monotonic / rows, col_monotonic / cols)
    elif cols >= 2:
        mono_score = row_monotonic / max(rows, 1)
    elif rows >= 2:
        mono_score = col_monotonic / max(cols, 1)
    else:
        mono_score = 0.0

    # Distinct-value adequacy.
    distinct_count = len(distinct)
    distinct_score = min(1.0, distinct_count / max(4, int(n**0.5) * 2))

    raw = (
        0.20 * bounded
        + 0.20 * non_trivial
        + 0.15 * row_smooth
        + 0.15 * col_smooth
        + 0.15 * mono_score
        + 0.15 * distinct_score
    )
    # Stripe penalty is expensive — only pay for it if the raw score is
    # high enough that the penalty could still leave us above noise.
    if raw < 0.4:
        return raw
    stripe = _stripe_penalty(values, cols, distinct_count)
    return raw * stripe


def _block_passes_hard_filters(values: list[int], cell_width: int = 2) -> bool:
    """Return *True* if a candidate block is plausible enough to score."""
    n = len(values)
    if n < _MIN_TABLE_CELLS:
        return False

    erasures = _erasures(cell_width)
    sentinels = _sentinels(cell_width)

    trivial = 0
    sentinel = 0
    lo = hi = values[0]
    distinct: set[int] = set()
    trivial_cap = int(n * _TABLE_TRIVIAL_FRACTION) + 1
    sentinel_cap = int(n * _TABLE_SENTINEL_FRACTION) + 1
    for v in values:
        if v in erasures:
            trivial += 1
            if trivial > trivial_cap:
                return False
        if v in sentinels:
            sentinel += 1
            if sentinel > sentinel_cap:
                return False
        if v < lo:
            lo = v
        elif v > hi:
            hi = v
        distinct.add(v)

    if lo == hi:
        return False

    d = len(distinct)
    if d / n < _TABLE_MIN_DISTINCT_RATIO and d < 6:
        return False

    return True


def _quick_trivial_fraction(buf: bytes, offset: int, byte_count: int) -> float:
    """Cheap byte-level erasure-fill estimate used to skip clearly-dead
    candidate regions before paying the full decode + score."""
    end = min(offset + byte_count, len(buf))
    if end <= offset:
        return 1.0
    chunk = buf[offset:end]
    z = chunk.count(0)
    f = chunk.count(0xFF)
    return (z + f) / len(chunk)


def _is_clearly_erased(
    buf: bytes, offset: int, byte_count: int, cell_width: int
) -> bool:
    """Cheap byte-level pre-filter for skipping erased / blank regions.

    For u8 cells a single byte equals a single cell, so we can be
    aggressive (>30%).  For u16 the high byte of any small value is
    zero, so we must reserve byte counting for catching wholesale
    flash erasure (>70%) and skip the filter otherwise.
    """
    frac = _quick_trivial_fraction(buf, offset, byte_count)
    if cell_width == 1:
        return frac > _TABLE_TRIVIAL_FRACTION
    return frac > 0.70


def _candidate_y_lens(
    y_values: tuple[int, ...] | None, y_max_len: int, min_y_length: int
) -> list[int]:
    """Return a small set of likely Y lengths to try.

    Instead of sweeping every possible truncation we look for sudden
    jumps in the step pattern — a real axis has roughly-uniform steps
    so a much larger step at index *k* means the greedy scanner
    absorbed values that don't belong to the axis.  We always include
    the full length, and a few common short ECU sizes that may be the
    "real" axis cropped by the scanner.
    """
    candidates: set[int] = set()
    if y_max_len >= min_y_length:
        candidates.add(y_max_len)
    if y_values is not None:
        n = len(y_values)
        steps = [y_values[i + 1] - y_values[i] for i in range(n - 1)]
        # Detect first index where step grows >= 4x median preceding step.
        for k in range(min_y_length - 1, len(steps)):
            window = steps[max(0, k - 3) : k]
            if not window:
                continue
            avg_prev = sum(window) / len(window)
            if avg_prev <= 0:
                continue
            if steps[k] >= avg_prev * 4 or steps[k] <= avg_prev / 4:
                # Truncate before the jump.
                ln = k + 1
                if min_y_length <= ln <= y_max_len:
                    candidates.add(ln)
    # A handful of common ECU axis sizes as safety net.
    for ln in (4, 5, 6, 8, 10, 12, 16):
        if min_y_length <= ln <= y_max_len:
            candidates.add(ln)
    # Sort descending so dedup naturally prefers larger blocks.
    return sorted(candidates, reverse=True)


def _try_pair_with_y(
    buf: bytes,
    *,
    x_axis_offset: int,
    x_len: int,
    y_axis_offset: int,
    y_max_len: int,
    byte_order: str,
    cell_width: int,
    min_y_length: int,
    min_score: float,
    x_quality: float,
    y_axis_values: tuple[int, ...] | None,
    max_padding: int,
) -> MapTable | None:
    """Try a small Y-truncation window + padding offsets for one (X, Y) pair.

    The greedy axis scanner can over-read by a handful of values at
    most, so we only sweep the top ``_MAX_Y_TRUNC`` Y lengths starting
    from ``y_max_len`` — bounded work per pair.
    """
    best: MapTable | None = None
    y_max_len = min(y_max_len, _MAX_Y_LEN)
    if y_max_len < min_y_length:
        return None
    buf_len = len(buf)
    for y_len in _candidate_y_lens(y_axis_values, y_max_len, min_y_length):
        y_end = y_axis_offset + y_len * 2
        if y_end > buf_len:
            continue
        y_values = tuple(y_axis_values[:y_len]) if y_axis_values is not None else None
        y_qual = _axis_quality(y_values) if y_values is not None else 0.5
        for pad in _PADDING_OFFSETS:
            if pad > max_padding:
                break
            data_start = y_end + pad
            cells = x_len * y_len
            byte_count = cells * cell_width
            if data_start + byte_count > buf_len:
                continue
            # Fast pre-filter: skip clearly erased blocks without decoding.
            if _is_clearly_erased(buf, data_start, byte_count, cell_width):
                continue
            block = _read_block(buf, data_start, cells, byte_order, cell_width)
            if block is None:
                continue
            if not _block_passes_hard_filters(block, cell_width):
                continue
            if cell_width >= 2 and _is_ascii_dense(buf, data_start, byte_count):
                continue
            raw = _score_table_block(block, x_len, cell_width)
            # Weight by axis quality — axis plausibility makes the
            # underlying detection less likely to be coincidental.
            score = raw * (0.7 + 0.15 * x_quality + 0.15 * y_qual)
            # Small tables (<= 16 cells) are statistically the most
            # prone to false positives — require a noticeably higher
            # score than the global floor to be reported.
            effective_min = min_score
            cells_count = x_len * y_len
            if cells_count <= 9:
                effective_min = max(min_score, 0.78)
            elif cells_count <= 16:
                effective_min = max(min_score, 0.72)
            elif cells_count <= 25:
                effective_min = max(min_score, 0.65)
            if score < effective_min:
                continue
            cand = MapTable(
                offset=data_start,
                cols=x_len,
                rows=y_len,
                cell_width=cell_width,
                byte_order=byte_order,
                x_axis_offset=x_axis_offset,
                y_axis_offset=y_axis_offset,
                score=score,
            )
            if best is None or cand.score > best.score:
                best = cand
    return best


def scan_map_tables(
    data: bytes,
    region: slice | None = None,
    axes: list[MapAxis] | None = None,
    min_score: float = 0.55,
    max_gap: int = 8,
    min_y_length: int = 3,
    min_axis_length: int = 4,
    cell_widths: tuple[int, ...] = (2, 1),
    max_results: int | None = 2000,
) -> list[MapTable]:
    """Scan *data* for plausible 2D calibration tables.

    Pairs axes returned by :func:`scan_map_axes` and promotes the bytes
    immediately following each pair into a `MapTable` candidate.  Also
    emits 1D / vector tables (single axis followed by a value run).

    The axis scanner is greedy — a detected axis may extend into the
    first few values of a following axis or into the data block itself.
    To compensate this function applies two truncation strategies:

    * **Y truncation** — tries every Y length from ``min_y_length`` up
      to the full detected length and keeps the highest-scoring data
      block.  (Carried over from the original implementation.)
    * **X truncation** — if no plausible Y axis follows the full X,
      tries shortening X by up to ``max_gap // 2`` values and re-scans
      from the truncated end for a Y axis that the greedy scanner may
      have absorbed into the X tail.

    Parameters
    ----------
    data:
        Raw ECU binary content.
    region:
        Optional ``slice`` to restrict scanning to a sub-region.
        Offsets in the returned `MapTable`s are relative to the region.
    axes:
        Pre-computed axis list.  If omitted, :func:`scan_map_axes` is
        called with its default parameters.
    min_score:
        Minimum heuristic score in ``[0, 1]`` for a candidate to be
        reported.  Defaults to ``0.55``.
    max_gap:
        Maximum bytes allowed between axis end and next axis / data
        start when pairing.  Defaults to ``8``.
    min_y_length:
        Minimum Y axis length considered during truncation.  Defaults
        to ``3``.
    min_axis_length:
        Minimum axis length passed to the greedy re-scan used for X
        truncation.  Defaults to ``4``.

    max_results:
        Optional hard cap on returned candidates after dedupe.  ``None``
        disables the cap.  Defaults to ``2000`` which is comfortably
        above the largest real ECU image table count and keeps the JSON
        payload bounded for UI consumers.

    Returns
    -------
    list[MapTable]
        Candidate tables sorted by descending score.  Overlapping
        candidates are deduplicated — the higher-scoring one wins.
    """
    buf = data[region] if region is not None else data

    if axes is None:
        axes = scan_map_axes(buf)

    if not axes:
        return []

    # Group axes by byte order — a table never mixes endians.
    by_order: dict[str, list[MapAxis]] = {"little": [], "big": []}
    for ax in axes:
        by_order[ax.byte_order].append(ax)
    for v in by_order.values():
        v.sort(key=lambda a: a.offset)

    candidates: list[MapTable] = []

    for byte_order, ordered in by_order.items():
        fmt = _FMT[byte_order]

        for i, x_axis in enumerate(ordered):
            # Yield to the OS scheduler every 64 axes so the scan
            # doesn't monopolise a CPU core for its full duration.
            if i % 64 == 0 and i > 0:
                time.sleep(0.002)
            x_end_full = x_axis.offset + x_axis.length * 2
            x_quality_full = _axis_quality(x_axis.values)

            # For each cell width, try the full sweep of pairings and
            # collect every passing candidate (multiple per X are fine;
            # dedup at the end picks the winners).
            for cell_width in cell_widths:
                strategy1_hit = False
                # ── Strategy 1: standard pairing (Y after full X) ──────────
                for y_axis in ordered[i + 1 :]:
                    gap = y_axis.offset - x_end_full
                    if gap < 0:
                        continue
                    if gap > max_gap:
                        break
                    cand = _try_pair_with_y(
                        buf,
                        x_axis_offset=x_axis.offset,
                        x_len=x_axis.length,
                        y_axis_offset=y_axis.offset,
                        y_max_len=min(y_axis.length, _MAX_Y_LEN),
                        byte_order=byte_order,
                        cell_width=cell_width,
                        min_y_length=min_y_length,
                        min_score=min_score,
                        x_quality=x_quality_full,
                        y_axis_values=y_axis.values,
                        max_padding=max_gap,
                    )
                    if cand is not None:
                        candidates.append(cand)
                        strategy1_hit = True

                # Strategy 2 is only useful when strategy 1 failed — it is
                # an order of magnitude more expensive than 1.
                if strategy1_hit:
                    continue

                # ── Strategy 2: X truncation (greedy-absorption recovery) ─
                # The axis scanner may have extended X past where it
                # should end.  Three sub-cases:
                #   2a. Absorbed bytes sit before a detected Y — reuse Y.
                #   2b. Absorbed bytes replaced Y entirely — re-scan.
                #   2c. Absorbed bytes are the data itself (axis bled
                #       into the surface) — emit as 1D for shorter X.
                n_trunc = min(max_gap, x_axis.length - min_axis_length)
                for trunc in range(1, n_trunc + 1):
                    x_len = x_axis.length - trunc
                    if x_len < min_axis_length:
                        break
                    x_end_trunc = x_axis.offset + x_len * 2
                    x_values_trunc = x_axis.values[:x_len]
                    x_quality_trunc = _axis_quality(x_values_trunc)

                    # 2a: pair shorter X with already-detected Y axes.
                    for y_axis in ordered[i + 1 :]:
                        gap_trunc = y_axis.offset - x_end_trunc
                        if gap_trunc < 0:
                            continue
                        if gap_trunc > max_gap:
                            break
                        cand = _try_pair_with_y(
                            buf,
                            x_axis_offset=x_axis.offset,
                            x_len=x_len,
                            y_axis_offset=y_axis.offset,
                            y_max_len=min(y_axis.length, _MAX_Y_LEN),
                            byte_order=byte_order,
                            cell_width=cell_width,
                            min_y_length=min_y_length,
                            min_score=min_score,
                            x_quality=x_quality_trunc,
                            y_axis_values=y_axis.values,
                            max_padding=max_gap,
                        )
                        if cand is not None:
                            candidates.append(cand)

                    # 2b: re-scan from truncated end for an absorbed Y.
                    run = _try_axis_at(
                        buf, x_end_trunc, fmt, min_y_length, _MAX_Y_LEN, 1, 10_000
                    )
                    if run >= min_y_length:
                        y_values_local = tuple(
                            struct.unpack_from(fmt, buf, x_end_trunc + k * 2)[0]
                            for k in range(run)
                        )
                        cand = _try_pair_with_y(
                            buf,
                            x_axis_offset=x_axis.offset,
                            x_len=x_len,
                            y_axis_offset=x_end_trunc,
                            y_max_len=min(run, _MAX_Y_LEN),
                            byte_order=byte_order,
                            cell_width=cell_width,
                            min_y_length=min_y_length,
                            min_score=min_score,
                            x_quality=x_quality_trunc,
                            y_axis_values=y_values_local,
                            max_padding=max_gap,
                        )
                        if cand is not None:
                            candidates.append(cand)

                # ── 1D candidate: X followed directly by a value row ──────
                # Skipped when 2D already accepted: dedup would drop them.
                if strategy1_hit:
                    continue
                for pad in _PADDING_OFFSETS:
                    if pad > max_gap:
                        break
                    data_start_1d = x_end_full + pad
                    byte_count_1d = x_axis.length * cell_width
                    if data_start_1d + byte_count_1d > len(buf):
                        continue
                    if _is_clearly_erased(
                        buf, data_start_1d, byte_count_1d, cell_width
                    ):
                        continue
                    values_1d = _read_block(
                        buf,
                        data_start_1d,
                        x_axis.length,
                        byte_order,
                        cell_width,
                    )
                    if values_1d is None:
                        continue
                    if not _block_passes_hard_filters(values_1d, cell_width):
                        continue
                    if cell_width >= 2 and _is_ascii_dense(
                        buf, data_start_1d, byte_count_1d
                    ):
                        continue
                    raw_1d = _score_table_block(values_1d, x_axis.length, cell_width)
                    score_1d = raw_1d * (0.7 + 0.15 * x_quality_full + 0.075)
                    if score_1d < min_score:
                        continue
                    candidates.append(
                        MapTable(
                            offset=data_start_1d,
                            cols=x_axis.length,
                            rows=1,
                            cell_width=cell_width,
                            byte_order=byte_order,
                            x_axis_offset=x_axis.offset,
                            y_axis_offset=None,
                            score=score_1d,
                        )
                    )

    # Dedup overlapping candidates — keep the highest-scoring one.
    #
    # We use **anchor / full-footprint** dedupe: any byte overlap in the
    # combined (X axis | Y axis | data) span between two candidates of
    # the same (byte_order, cell_width) causes the lower-scored one to
    # be dropped.  This is much stricter than the previous IoU(>25%)
    # rule, which let through cluster duplicates that were only shifted
    # by a few rows (e.g. the 0x269344 / 0x269544 / 0x269744 cluster
    # seen on real ECU dumps).
    #
    # Sort key: score (rounded to 2 dp so near-ties prefer larger and
    # 2D blocks), then area, then 2D-over-1D tiebreak, then offset for
    # stability.
    candidates.sort(
        key=lambda t: (
            round(t.score, 2),
            t.cols * t.rows,
            1 if t.rows > 1 else 0,
            -t.offset,
        ),
        reverse=True,
    )

    def _footprint(t: MapTable) -> tuple[int, int]:
        """Return (start, end) byte range claimed by candidate *t*."""
        data_end = t.offset + t.cols * t.rows * t.cell_width
        start = t.x_axis_offset
        if t.y_axis_offset is not None:
            start = min(start, t.y_axis_offset)
        start = min(start, t.offset)
        end = max(data_end, t.x_axis_offset + t.cols * 2)
        if t.y_axis_offset is not None:
            end = max(end, t.y_axis_offset + t.rows * 2)
        return start, end

    # Single global claim list — different (byte_order, cell_width)
    # interpretations of the same bytes can't both be real tables, so
    # we dedupe across all candidates regardless of bucket.
    claimed_ranges: list[tuple[int, int]] = []
    chosen: list[MapTable] = []

    for cand in candidates:
        cand_start, cand_end = _footprint(cand)
        # Any overlap at all with an already-chosen footprint → reject.
        # Winner list stays small in practice (<a few hundred), so a
        # linear scan is fine.
        overlapped = False
        for s, e in claimed_ranges:
            if cand_start < e and s < cand_end:
                overlapped = True
                break
        if overlapped:
            continue
        chosen.append(cand)
        claimed_ranges.append((cand_start, cand_end))
        if max_results is not None and len(chosen) >= max_results:
            break

    return chosen


# ============================================================================
# Rust acceleration — dispatches to native backend when available.
# ============================================================================

import os as _os

_py_scan_map_axes = scan_map_axes
_py_scan_map_tables = scan_map_tables

if _os.environ.get("OPENREMAP_FORCE_PYTHON", "").strip() not in ("1", "true", "yes"):
    try:
        from openremap._rust import scan_map_axes as _rs_scan_map_axes
        from openremap._rust import scan_map_tables as _rs_scan_map_tables
        _MAP_BACKEND = "rust"
    except ImportError:
        _MAP_BACKEND = "python"
else:
    _MAP_BACKEND = "python"


def map_hunter_backend() -> str:
    """Return which backend is active: ``"rust"`` or ``"python"``."""
    return _MAP_BACKEND


if _MAP_BACKEND == "rust":
    def scan_map_axes(  # type: ignore[no-redef]
        data: bytes,
        region: slice | None = None,
        min_axis_length: int = 4,
        max_axis_length: int = 32,
        min_step: int = 1,
        max_step: int = 10_000,
    ):
        rs_start = -1
        rs_end = -1
        if region is not None:
            rs_start = region.start if region.start is not None else -1
            rs_end = region.stop if region.stop is not None else -1
        raw = _rs_scan_map_axes(data, rs_start, rs_end, min_axis_length,
                                max_axis_length, min_step, max_step)
        return [MapAxis(offset=o, length=l, byte_order=bo, values=tuple(v))
                for (o, l, bo, v) in raw]

    def scan_map_tables(  # type: ignore[no-redef]
        data: bytes,
        region: slice | None = None,
        axes: list | None = None,
        min_score: float = 0.55,
        max_gap: int = 8,
        min_y_length: int = 3,
        min_axis_length: int = 4,
        cell_widths: tuple[int, ...] = (2, 1),
        max_results: int | None = 2000,
    ):
        rs_start = -1
        rs_end = -1
        if region is not None:
            rs_start = region.start if region.start is not None else -1
            rs_end = region.stop if region.stop is not None else -1

        rs_axes = None
        if axes is not None:
            rs_axes = [(a.offset, a.length, a.byte_order, list(a.values)) for a in axes]

        raw = _rs_scan_map_tables(
            data, rs_start, rs_end, rs_axes, min_score, max_gap,
            min_y_length, min_axis_length, list(cell_widths), max_results,
        )
        return [MapTable(offset=o, cols=c, rows=r, cell_width=cw,
                        byte_order=bo, x_axis_offset=xo, y_axis_offset=yo, score=s)
                for (o, c, r, cw, bo, xo, yo, s) in raw]
