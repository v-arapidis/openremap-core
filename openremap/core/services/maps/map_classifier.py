"""
Calibration map content classifier — probabilistic labels for scanned tables.

Guesses a map's purpose from axis shapes, cell-surface trends, and
dimensionality — WITHOUT manufacturer catalogs.  Labels are probabilistic
(``fuel 0.72``), never verified names: A2L/DAMOS data (todo item 11) would
upgrade a label to a verified map name, not replace this layer.

Signals
-------
- Axis profiles: RPM-shaped (0..~7000), load-shaped (0..~120), pressure-shaped
  (0..~2600), speed-shaped (0..~300), throttle-shaped (0..100).
- Cell-surface trends: mean-per-row slope along the load axis, mean-per-column
  slope along the RPM axis, plateau fraction (cells pinned at the maximum).
- Dimensionality: 1D vs 2D, flat-Y (identical rows), compound halves (stride).

Fuel-family context (from the manufacturer extractors) gates the label set:
diesel ECUs have no spark-timing maps, petrol ECUs have no duration maps.
"""

from __future__ import annotations

import struct

from openremap.core.services.maps.map_hunter import MapTable

# ---------------------------------------------------------------------------
# Label vocabulary
# ---------------------------------------------------------------------------

LABEL_FUEL = "fuel"
LABEL_TIMING = "timing"
LABEL_BOOST = "boost"
LABEL_TORQUE = "torque"
LABEL_DURATION = "duration"
LABEL_UNKNOWN = "unknown"

ALL_LABELS = (LABEL_FUEL, LABEL_TIMING, LABEL_BOOST, LABEL_TORQUE, LABEL_DURATION)

# Family name prefixes → fuel type.  Used to gate the label set.
_DIESEL_FAMILIES = (
    "EDC1", "EDC3", "EDC15", "EDC16", "EDC17",
    "PPD", "SID801", "SID803", "MJD6JF", "EMS2000", "Multec",
)
_PETROL_FAMILIES = (
    "ME7", "ME9", "ME155", "M1", "M3", "M4", "M5", "Mono", "MP9",
    "LH", "IAW", "SIMOS", "Simtec", "EMS",
)


def family_fuel_type(family: str | None) -> str | None:
    """``'diesel'`` / ``'petrol'`` / ``None`` (unknown — generic priors)."""
    if not family:
        return None
    if any(family.startswith(p) for p in _DIESEL_FAMILIES):
        return "diesel"
    if any(family.startswith(p) for p in _PETROL_FAMILIES):
        return "petrol"
    return None


# ---------------------------------------------------------------------------
# Data reading
# ---------------------------------------------------------------------------


def _read_axis(data: bytes, offset: int, count: int, byte_order: str) -> list[int]:
    fmt = f"{'<' if byte_order == 'little' else '>'}{count}H"
    return list(struct.unpack_from(fmt, data, offset))


def _read_cells(data: bytes, t: MapTable) -> list[int]:
    """Read all cells of *t* — handles compound (strided) halves."""
    cols, rows, cw = t.cols, t.rows, t.cell_width
    le = t.byte_order == "little"
    row_bytes = cols * cw
    if t.stride is None or t.stride == row_bytes:
        count = cols * rows
        if cw == 1:
            return list(data[t.offset : t.offset + count])
        fmt = f"{'<' if le else '>'}{count}H"
        return list(struct.unpack_from(fmt, data, t.offset))
    out: list[int] = []
    for r in range(rows):
        off = t.offset + r * t.stride
        if cw == 1:
            out.extend(data[off : off + cols])
        else:
            fmt = f"{'<' if le else '>'}{cols}H"
            out.extend(struct.unpack_from(fmt, data, off))
    return out


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return (s[n // 2] + s[(n - 1) // 2]) / 2.0


def _axis_features(vals: list[int]) -> dict:
    steps = [b - a for a, b in zip(vals, vals[1:])]
    med = _median(steps)
    span = vals[-1] - vals[0] if vals else 0
    linearity = 1.0
    if med > 0 and steps:
        linearity = max(0.0, 1.0 - (max(steps) - min(steps)) / (4.0 * med))
    return {
        "n": len(vals),
        "first": vals[0] if vals else 0,
        "last": vals[-1] if vals else 0,
        "span": span,
        "median_step": med,
        "linearity": linearity,
    }


def _trap(x: float, lo: float, hi: float) -> float:
    """Soft trapezoid membership: 0 below lo, ramps to 1 at hi."""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def _band(x: float, lo: float, hi: float) -> float:
    """Two-sided soft window: 1 inside [lo, hi], ramping to 0 outside.

    Falls to 0 at ``lo / 2`` below and ``hi * 1.5`` above — a 6000 RPM
    axis must NOT get full "load-shaped" membership.
    """
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        return _trap(x, lo / 2.0, lo)
    return 1.0 - _trap(x, hi, hi * 1.5)


def _axis_profile_score(f: dict) -> tuple[str, float]:
    """Classify an axis as rpm/load/pressure/speed/throttle-shaped."""
    scores = {
        "rpm": 0.8 * _band(f["last"], 2800, 7000) * _trap(f["first"], -1, 1200)
        + 0.2 * _band(f["median_step"], 100, 500),
        "load": 0.7 * _band(f["last"], 30, 130) * _trap(f["first"], -1, 30)
        + 0.3 * _band(f["median_step"], 2, 15),
        "pressure": 0.7 * _band(f["last"], 1800, 2600) * _trap(f["first"], -1, 500)
        + 0.3 * _band(f["median_step"], 40, 250),
        "speed": 0.7 * _band(f["last"], 150, 280) * _trap(f["first"], -1, 20)
        + 0.3 * _band(f["median_step"], 10, 60),
        "throttle": 0.7 * _band(f["last"], 60, 100) * _trap(f["first"], -1, 20)
        + 0.3 * _band(f["median_step"], 2, 10),
    }
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 0.3:
        return "other", best[1]
    return best


def _surface_trends(cells: list[int], cols: int, rows: int) -> dict:
    """Row/column mean slopes and plateau fraction."""
    if rows == 0 or cols == 0:
        return {"row_slope": 0.0, "col_slope": 0.0, "plateau": 0.0}
    row_means = [
        sum(cells[r * cols : (r + 1) * cols]) / cols for r in range(rows)
    ]
    col_means = [
        sum(cells[r * cols + c] for r in range(rows)) / rows for c in range(cols)
    ]
    # Normalised linear slope: covariance-ish, in [-1, 1].
    def slope(means: list[float]) -> float:
        n = len(means)
        if n < 2:
            return 0.0
        xs = list(range(n))
        mx, my = sum(xs) / n, sum(means) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, means))
        den_x = sum((x - mx) ** 2 for x in xs)
        den_y = sum((y - my) ** 2 for y in means)
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / ((den_x * den_y) ** 0.5)
    cell_max = max(cells)
    plateau = sum(1 for v in cells if v >= 0.98 * cell_max) / len(cells) if cells else 0.0
    return {
        "row_slope": slope(row_means),
        "col_slope": slope(col_means),
        "plateau": plateau,
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_table(
    data: bytes,
    table: MapTable,
    fuel_type: str | None = None,
) -> tuple[str, float]:
    """Return ``(label, confidence)`` for a single table.

    Labels are probabilistic: ``fuel 0.72`` means "looks like a fuel map"
    with 72% confidence — never a verified map name.
    """
    x_prof = "other"
    x_conf = 0.0
    if table.x_axis_offset is not None:
        x_vals = _read_axis(data, table.x_axis_offset, table.cols, table.byte_order)
        x_prof, x_conf = _axis_profile_score(_axis_features(x_vals))
    y_prof, y_conf = "other", 0.0
    if table.y_axis_offset is not None and table.rows > 1:
        y_vals = _read_axis(data, table.y_axis_offset, table.rows, table.byte_order)
        y_prof, y_conf = _axis_profile_score(_axis_features(y_vals))

    cells = _read_cells(data, table)
    trends = _surface_trends(cells, table.cols, table.rows)
    span = max(cells) - min(cells) if cells else 0
    mean = sum(cells) / len(cells) if cells else 0
    rel_span = span / mean if mean else 0.0

    is_1d = table.rows == 1 or table.y_axis_offset is None
    flat_y = (
        table.rows > 1
        and all(
            cells[r * table.cols : (r + 1) * table.cols]
            == cells[: table.cols]
            for r in range(1, table.rows)
        )
    )

    scores: dict[str, float] = {}

    # Torque / demand: 1D or plateau-heavy flat-topped surfaces.
    scores[LABEL_TORQUE] = (
        0.35 * (1.0 if is_1d else 0.3)
        + 0.35 * min(trends["plateau"] * 1.5, 1.0)
        + 0.15 * (1.0 if x_prof == "rpm" else 0.2)
        + 0.15 * (1.0 if flat_y else 0.3)
    )

    if not is_1d:
        rpm_axis = 1.0 if x_prof == "rpm" else 0.2
        load_axis = 1.0 if y_prof in ("load", "throttle") else 0.2
        # Fuel: rises with load, falls (or dips) with RPM.
        scores[LABEL_FUEL] = (
            0.3 * rpm_axis
            + 0.2 * load_axis
            + 0.3 * max(0.0, trends["row_slope"])
            + 0.1 * max(0.0, -trends["col_slope"])
            + 0.1 * (1.0 if 0.03 < rel_span < 1.5 else 0.2)
        )
        # Timing: falls with load, rises with RPM — mirror of fuel.
        scores[LABEL_TIMING] = (
            0.3 * rpm_axis
            + 0.2 * load_axis
            + 0.3 * max(0.0, -trends["row_slope"])
            + 0.1 * max(0.0, trends["col_slope"])
            + 0.1 * (1.0 if 0.03 < rel_span < 1.5 else 0.2)
        )
        # Boost: rises with both axes.
        scores[LABEL_BOOST] = (
            0.3 * rpm_axis
            + 0.2 * load_axis
            + 0.3 * max(0.0, trends["row_slope"])
            + 0.2 * max(0.0, trends["col_slope"])
        )
        # Duration (diesel injector time): steep rise with load.
        scores[LABEL_DURATION] = (
            0.25 * rpm_axis
            + 0.2 * load_axis
            + 0.45 * max(0.0, trends["row_slope"])
            + 0.1 * (1.0 if rel_span > 0.4 else 0.2)
        )
    else:
        scores[LABEL_FUEL] = 0.0
        scores[LABEL_TIMING] = 0.0
        scores[LABEL_BOOST] = 0.0
        scores[LABEL_DURATION] = 0.0

    # Family gating: diesel ECUs have no spark timing; petrol no duration.
    if fuel_type == "diesel":
        scores[LABEL_TIMING] = 0.0
        scores[LABEL_TORQUE] *= 1.1
    elif fuel_type == "petrol":
        scores[LABEL_DURATION] = 0.0
        scores[LABEL_TIMING] *= 1.1

    best_label = max(scores, key=lambda k: scores[k])
    best_score = scores[best_label]
    if best_score < 0.45:
        return LABEL_UNKNOWN, round(best_score, 2)
    return best_label, round(min(best_score, 1.0), 2)


def classify_tables(
    data: bytes,
    tables: list[MapTable],
    fuel_type: str | None = None,
) -> dict[int, tuple[str, float]]:
    """Classify a list of tables; keyed by data offset.

    Returns ``{offset: (label, confidence)}`` — offsets are unique per
    table in a single scan (the scanner deduplicates data ranges).
    """
    return {
        t.offset: classify_table(data, t, fuel_type=fuel_type)
        for t in tables
    }
