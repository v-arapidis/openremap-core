"""
Deterministic synthetic ECU dump generator.

Scope: produce binaries that exercise the **engine** (map hunting, layout
segmentation, diff, cook/tune, checksums) with a known ground truth, without
touching a single byte of real ROM data.  The table layout is the one real
WinOLS/DAMOS tables use:

    [ X axis (cols * 2 B) | Y axis (rows * 2 B) | cells (cols * rows * cell_w B) ]

Axes are strictly increasing u16 values (length 4–32, step 1–10000) because
that is what :func:`openremap.core.services.maps.map_hunter.scan_map_axes`
looks for; cells form a smooth surface so the table scores above the default
``min_score``.  Ground truth (offset, shape, byte order, axis values) is
returned alongside the bytes so tests can assert *what* was found, not merely
that output matched a snapshot.

Not in scope: manufacturer fingerprints.  Generated files are deliberately
anonymous — ``identify`` reports ``Unknown`` for them, and that negative result
is itself worth pinning.
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
from dataclasses import dataclass, field, replace
from typing import Sequence

# Sector kinds.  "code" is high-entropy pseudo-code, "calibration" holds the
# planted maps, "erased" is a uniformly blank flash sector (what the segmenter
# must label as erased).  Calibration sectors get a *non-erased* background so
# the segmenter reads them as data.
CODE = "code"
CALIBRATION = "calibration"
ERASED = "erased"
SECTOR_KINDS = (CODE, CALIBRATION, ERASED)

DEFAULT_LAYOUT: tuple[str, ...] = (CODE, CALIBRATION, ERASED, CODE)

# Bumped whenever this generator's *output* changes for the same parameters.
# The corpus manifest records the same number, so a golden diff can always be
# attributed: engine changed (version unchanged) vs generator changed (bumped).
GENERATOR_VERSION = 1

# Deterministic shape palette: (cols, rows).  Chosen empirically: every shape
# here is detected **exactly** (offset, cols, rows, cell width, byte order) by
# ``scan_map_tables`` for both cell widths and both byte orders — see
# ``tests/tuning/services/test_synth_generator.py``.  A 16x12 u8 table is
# deliberately absent: the hunter truncates it to 16x10 rows (recorded as a
# known observation, not a supported shape).
_SHAPES: tuple[tuple[int, int], ...] = (
    (8, 8),
    (10, 6),
    (12, 8),
    (16, 8),
    (20, 10),
    (24, 5),
    (32, 16),
    (12, 20),
    (6, 5),
)

_X_START, _X_STEP = 500, 250        # rpm-like axis
_Y_START, _Y_STEP = 0, 100          # load-like axis
_MAX_U16 = 0xFFFF

# Written before each planted map so a strictly-increasing run cannot extend
# backwards into the background and shift the axis start.
_BARRIER = b"\xff" * 8
_MAP_ALIGN = 16
_MAP_GAP = 64


@dataclass(frozen=True)
class Sector:
    """A flash-layout region, as planted (ground truth for the segmenter)."""

    kind: str
    start: int
    end: int  # exclusive

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class PlantedMap:
    """A calibration table this generator wrote — the expected detection."""

    map_id: int
    offset: int                  # first cell byte
    cols: int
    rows: int
    cell_width: int
    byte_order: str
    x_axis_offset: int
    y_axis_offset: int
    x_axis: tuple[int, ...]
    y_axis: tuple[int, ...]
    cells: tuple[int, ...]       # row-major, ``cols * rows`` values
    sector: int                  # index into ``GeneratedDump.sectors``

    @property
    def end(self) -> int:
        return self.offset + self.cols * self.rows * self.cell_width


@dataclass(frozen=True)
class GeneratedDump:
    """A generated binary plus the ground truth of what was planted."""

    data: bytes
    maps: tuple[PlantedMap, ...]
    sectors: tuple[Sector, ...]
    seed: int
    params: dict = field(default_factory=dict)
    # ``{map_id: cells_changed}`` — filled by :meth:`tuned`, empty for a stock.
    changes: dict = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def is_stock(self) -> bool:
        return not self.changes

    def calibration_sectors(self) -> tuple[Sector, ...]:
        return tuple(s for s in self.sectors if s.kind == CALIBRATION)

    def map_at(self, offset: int) -> PlantedMap | None:
        """Return the planted map whose cell block starts at *offset*."""
        for m in self.maps:
            if m.offset == offset:
                return m
        return None

    def tuned(
        self, percent: float = 5.0, *, maps: Sequence[int] | None = None
    ) -> "GeneratedDump":
        """Return a *tuned* copy: shift the cells of the selected maps.

        Deterministic (its RNG derives from the dump's seed) and everything
        outside those cell blocks stays byte-identical — so
        ``(stock, stock.tuned())`` is a valid cook pair with a recorded ground
        truth of what moved.
        """
        chosen = set(range(len(self.maps))) if maps is None else set(maps)
        rng = random.Random((self.seed << 8) ^ 0x7A17)
        buf = bytearray(self.data)
        new_maps: list[PlantedMap] = []
        changes: dict[int, int] = {}

        for m in self.maps:
            if m.map_id not in chosen:
                new_maps.append(m)
                continue
            limit = 0xFF if m.cell_width == 1 else _MAX_U16
            cells: list[int] = []
            for v in m.cells:
                # Direction-mixed shift, clamped — the shape of a real tune.
                delta = round(v * (percent / 100.0) * (1 if rng.random() < 0.6 else -1))
                cells.append(max(0, min(limit, v + delta)))
            buf[m.offset:m.end] = _pack_cells(cells, m.cell_width, m.byte_order)
            changed = sum(1 for a, b in zip(m.cells, cells) if a != b)
            if changed:
                changes[m.map_id] = changed
            new_maps.append(replace(m, cells=tuple(cells)))

        return replace(self, data=bytes(buf), maps=tuple(new_maps), changes=changes)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_dump(
    *,
    size: int = 256 * 1024,
    maps: int = 1,
    seed: int = 7,
    layout: Sequence[str] = DEFAULT_LAYOUT,
    cell_width: int = 2,
    byte_order: str = "little",
) -> GeneratedDump:
    """Generate a deterministic ECU-like dump.

    Parameters
    ----------
    size:
        Total size in bytes, split evenly across the *layout* sectors.
    maps:
        How many calibration tables to plant in the calibration sector(s).
        Shapes cycle through a fixed palette so cases stay reproducible.
    seed:
        Seeds the background noise; identical inputs give identical bytes.
    layout:
        Sector kinds, left to right — a non-empty selection of ``"code"``,
        ``"calibration"``, ``"erased"``.
    cell_width:
        ``2`` for 16-bit cells, ``1`` for 8-bit.
    byte_order:
        ``"little"`` or ``"big"`` — applies to axes and cells alike.

    Returns
    -------
    GeneratedDump
        The bytes plus the planted ground truth.

    Raises
    ------
    ValueError
        On an unsupported size/parameter, a layout without a calibration
        sector when *maps* > 0, or a calibration sector too small to hold the
        requested maps.
    """
    if size < 4096:
        raise ValueError("size must be at least 4096 bytes")
    if maps < 0:
        raise ValueError("maps must be >= 0")
    if cell_width not in (1, 2):
        raise ValueError("cell_width must be 1 or 2")
    if byte_order not in ("little", "big"):
        raise ValueError("byte_order must be 'little' or 'big'")
    if not layout or any(k not in SECTOR_KINDS for k in layout):
        raise ValueError(f"layout must be a non-empty sequence of {SECTOR_KINDS}")

    rng = random.Random(seed)
    buf = bytearray(size)

    sectors = _split_sectors(size, layout)
    for sec in sectors:
        if sec.kind in (CODE, CALIBRATION):
            buf[sec.start:sec.end] = rng.randbytes(sec.size)
        # ERASED keeps its zero fill.

    calib = [(i, s) for i, s in enumerate(sectors) if s.kind == CALIBRATION]
    if maps and not calib:
        raise ValueError("layout has no calibration sector to plant maps in")

    placed: list[PlantedMap] = []
    cursors: dict[int, int] = {}
    for i in range(maps):
        sec_idx, sec = calib[i % len(calib)]
        cols, rows = _SHAPES[i % len(_SHAPES)]
        cell_bytes = cols * rows * cell_width

        start = max(cursors.get(sec_idx, sec.start), sec.start + _MAP_ALIGN)
        start = _align(start)
        if start + len(_BARRIER) + 2 * cols + 2 * rows + cell_bytes + _MAP_GAP > sec.end:
            raise ValueError(
                f"calibration sector {sec.start:#x}-{sec.end:#x} is too small "
                f"for {i + 1} map(s) of shape {cols}x{rows}; enlarge the dump or "
                f"plant fewer maps"
            )

        buf[start:start + len(_BARRIER)] = _BARRIER
        planted = _write_map(
            buf, start + len(_BARRIER), i, cols, rows, cell_width, byte_order, sec_idx
        )
        cursors[sec_idx] = planted.end + _MAP_GAP
        placed.append(planted)

    return GeneratedDump(
        data=bytes(buf),
        maps=tuple(placed),
        sectors=sectors,
        seed=seed,
        params={
            "size": size,
            "maps": maps,
            "seed": seed,
            "layout": list(layout),
            "cell_width": cell_width,
            "byte_order": byte_order,
        },
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _split_sectors(size: int, layout: Sequence[str]) -> tuple[Sector, ...]:
    n = len(layout)
    base = size // n
    out: list[Sector] = []
    start = 0
    for i, kind in enumerate(layout):
        end = size if i == n - 1 else start + base
        out.append(Sector(kind=kind, start=start, end=end))
        start = end
    return tuple(out)


def _align(offset: int) -> int:
    return (offset + _MAP_ALIGN - 1) // _MAP_ALIGN * _MAP_ALIGN


def _write_map(
    buf: bytearray,
    offset: int,
    map_id: int,
    cols: int,
    rows: int,
    cell_width: int,
    byte_order: str,
    sector: int,
) -> PlantedMap:
    x_axis = tuple(_X_START + i * _X_STEP for i in range(cols))
    y_axis = tuple(_Y_START + i * _Y_STEP for i in range(rows))
    cells = tuple(
        _surface(xi, yi, rows, cell_width)
        for yi in range(rows)
        for xi in range(cols)
    )

    buf[offset:offset + 2 * cols] = _pack_u16(x_axis, byte_order)
    y_off = offset + 2 * cols
    buf[y_off:y_off + 2 * rows] = _pack_u16(y_axis, byte_order)
    data_off = y_off + 2 * rows
    packed = _pack_cells(cells, cell_width, byte_order)
    buf[data_off:data_off + len(packed)] = packed

    return PlantedMap(
        map_id=map_id,
        offset=data_off,
        cols=cols,
        rows=rows,
        cell_width=cell_width,
        byte_order=byte_order,
        x_axis_offset=offset,
        y_axis_offset=y_off,
        x_axis=x_axis,
        y_axis=y_axis,
        cells=cells,
        sector=sector,
    )


def _surface(xi: int, yi: int, rows: int, cell_width: int) -> int:
    """A smooth calibration surface, monotonic in both axes.

    Deliberately *not* an oscillating function: a sine-like surface scores
    badly with the map hunter (its local extrema read as competing structure,
    and large tables were missed entirely), while a rising load
    (``y``) × rpm (``x``) surface with a mild ripple is both realistic — it is
    the shape of a torque/boost map — and detected exactly.  Verified across
    the whole shape palette, both cell widths and both byte orders.
    """
    span = max(1, rows - 1)
    value = (
        300.0
        + 700.0 * (yi / span)
        + 55.0 * xi
        + 25.0 * math.sin(xi / 3.0 + yi / 4.0)
    )
    if cell_width == 1:
        value /= 14.0
        return max(0, min(0xFF, round(value)))
    return max(0, min(_MAX_U16, round(value)))


def _pack_u16(values: Sequence[int], byte_order: str) -> bytes:
    fmt = ("<" if byte_order == "little" else ">") + f"{len(values)}H"
    return struct.pack(fmt, *values)


def _pack_cells(values: Sequence[int], cell_width: int, byte_order: str) -> bytes:
    if cell_width == 1:
        return bytes(values)
    return _pack_u16(values, byte_order)
