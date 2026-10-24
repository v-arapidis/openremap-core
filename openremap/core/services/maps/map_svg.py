"""
A calibration table as a picture: SVG out, no dependencies.

A terminal cannot show a map honestly — character cells, fonts, 24-bit colour and
aspect ratio are all out of our control — so the picture is produced as a file
instead of as characters. SVG is the right target: plain text, no image library,
scales to any size, prints, opens in a browser, and the same output can be
embedded by the GUI later.

What the picture says is deliberately narrow: **the numbers in the cells**,
coloured by value, with the axes that were read from the binary. It does not
label the map (the scanner's label is a guess) and it does not smooth anything
between cells.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

__all__ = ["render_map_svg", "value_color"]

RGB = tuple[int, int, int]

#: WinOLS-style ramp, blue at the floor and red at the peak — the same hue sweep
#: as the studio 3D view, so a table looks like the same table everywhere.
_SATURATION = 0.88
_LIGHT_FLOOR = 0.35
_LIGHT_TOP = 0.22

_BACKDROP = "#0e141b"
_PANEL = "#141c26"
_INK = "#e8eef7"
_INK_DIM = "#8aa0c0"
_INK_FAINT = "#5a6b85"
_RULE = "#25313f"


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    t %= 1.0
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def value_color(t: float) -> RGB:
    """Map a normalised value ``0..1`` onto the map ramp."""
    t = min(1.0, max(0.0, t))
    hue = 0.667 * (1.0 - t)
    light = _LIGHT_FLOOR + t * _LIGHT_TOP
    q = light * (1 + _SATURATION) if light < 0.5 else light + _SATURATION - light * _SATURATION
    p = 2 * light - q
    return (
        round(_hue_to_rgb(p, q, hue + 1 / 3) * 255),
        round(_hue_to_rgb(p, q, hue) * 255),
        round(_hue_to_rgb(p, q, hue - 1 / 3) * 255),
    )


def _hex(rgb: RGB) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _ink_on(rgb: RGB) -> str:
    """Dark text on bright cells, light text on dark ones."""
    luminance = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
    return "#0b1016" if luminance > 0.55 else _INK


def _label_values(axis: Sequence[int] | None, count: int, wanted: int = 8) -> list[tuple[int, int]]:
    """Pick (index, value) pairs to label — at most *wanted* of them."""
    if not axis or len(axis) < count:
        step = max(1, math.ceil(count / wanted))
        return [(i, i) for i in range(0, count, step)]
    step = max(1, math.ceil(count / wanted))
    return [(i, int(axis[i])) for i in range(0, count, step)]


def render_map_svg(
    cells: Sequence[Sequence[int]],
    *,
    title: str,
    facts: Sequence[str] = (),
    notes: Sequence[str] = (),
    x_axis: Sequence[int] | None = None,
    y_axis: Sequence[int] | None = None,
    axis_source: str = "index",
    include_values: bool | None = None,
    width: int = 960,
) -> str:
    """Render *cells* as an SVG document.

    Parameters
    ----------
    cells:
        Row-major cell values, as read from the binary.
    title:
        First line of the header (e.g. the dump's name and the table offset).
    facts:
        Short label/value-ish strings shown under the title (shape, score…).
    notes:
        Lines at the bottom: what the picture does *not* claim.
    x_axis, y_axis:
        Breakpoint values read from the binary, or ``None`` for cell indices.
    axis_source:
        ``"stored"`` when the axes came from the binary, ``"index"`` otherwise —
        printed in the footer so nobody mistakes indices for real breakpoints.
    include_values:
        Write each cell's number inside the cell. Default: yes for tables up to
        24×24 (readable), no above it.
    width:
        Total image width in pixels; grows with the table, never shrinks below.
    """
    rows = len(cells)
    cols = max((len(row) for row in cells), default=0)
    if rows == 0 or cols == 0:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="120">'
            f'<rect width="100%" height="100%" fill="{_BACKDROP}"/>'
            f'<text x="24" y="60" fill="{_INK}" font-family="monospace" font-size="14">'
            f"{escape(title)} — no cells to draw</text></svg>"
        )

    values = [int(cell) for row in cells for cell in row]
    low, high = min(values), max(values)
    span = (high - low) or 1
    if include_values is None:
        include_values = rows <= 24 and cols <= 24

    # Geometry. Values are written into cells when requested, so cells need room.
    cell = max(22, min(56, 720 // max(cols, 1)))
    plot_w = cell * cols
    plot_h = cell * rows
    axis_w = 78  # y breakpoints
    axis_h = 44  # x breakpoints
    bar_w = 68  # colour scale
    margin = 24
    header_h = 66 + 17 * max(1, len(facts))
    footer_h = 30 + 16 * max(1, len(notes))
    total_w = max(width, margin * 2 + axis_w + plot_w + 32 + bar_w)
    total_h = header_h + plot_h + axis_h + footer_h
    plot_x = margin + axis_w
    plot_y = header_h

    font = "ui-monospace, SFMono-Regular, Menlo, monospace"
    opening = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}" font-family="{font}">'
    )
    heading = (
        f'<text x="{margin}" y="38" fill="{_INK}" font-size="19" font-weight="600">'
        f"{escape(title)}</text>"
    )
    out: list[str] = [
        opening,
        f'<rect width="{total_w}" height="{total_h}" fill="{_BACKDROP}"/>',
        heading,
    ]
    # One fact per line: fixed columns collide as soon as a label is long.
    for index, fact in enumerate(facts):
        out.append(
            f'<text x="{margin}" y="{60 + index * 17}" '
            f'fill="{_INK_DIM}" font-size="12">{escape(fact)}</text>'
        )

    # Cells.
    out.append(f'<rect x="{plot_x}" y="{plot_y}" width="{plot_w}" height="{plot_h}" fill="{_PANEL}"/>')
    for r, row in enumerate(cells):
        for c in range(cols):
            value = int(row[c]) if c < len(row) else low
            color = value_color((value - low) / span)
            x = plot_x + c * cell
            y = plot_y + r * cell
            out.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{_hex(color)}" '
                f'stroke="{_BACKDROP}" stroke-width="0.5"/>'
            )
            if include_values:
                size = max(8, min(13, cell // 3 if len(str(value)) > 3 else cell // 2))
                out.append(
                    f'<text x="{x + cell / 2:.1f}" y="{y + cell / 2 + size / 3:.1f}" '
                    f'fill="{_ink_on(color)}" font-size="{size}" text-anchor="middle">'
                    f"{value}</text>"
                )

    # The peak cell, outlined — the one fact a colour scale hides at a glance.
    peak = max(
        ((r, c) for r, row in enumerate(cells) for c in range(len(row))),
        key=lambda rc: int(cells[rc[0]][rc[1]]),
    )
    out.append(
        f'<rect x="{plot_x + peak[1] * cell + 1}" y="{plot_y + peak[0] * cell + 1}" '
        f'width="{cell - 2}" height="{cell - 2}" fill="none" stroke="{_INK}" '
        f'stroke-width="1.5"/>'
    )

    # Frame.
    out.append(
        f'<rect x="{plot_x}" y="{plot_y}" width="{plot_w}" height="{plot_h}" '
        f'fill="none" stroke="{_RULE}"/>'
    )

    # Axes: breakpoint values when they were read, cell indices otherwise.
    for index, value in _label_values(y_axis, rows):
        y = plot_y + index * cell + cell / 2 + 4
        out.append(
            f'<text x="{plot_x - 8}" y="{y:.1f}" fill="{_INK_DIM}" font-size="11" '
            f'text-anchor="end">{value}</text>'
        )
    for index, value in _label_values(x_axis, cols):
        x = plot_x + index * cell + cell / 2
        out.append(
            f'<text x="{x:.1f}" y="{plot_y + plot_h + 20}" fill="{_INK_DIM}" font-size="11" '
            f'text-anchor="middle">{value}</text>'
        )
    out.append(
        f'<text x="{plot_x + plot_w / 2:.1f}" y="{plot_y + plot_h + 38}" '
        f'fill="{_INK_FAINT}" font-size="11" text-anchor="middle">'
        f'{"column breakpoints" if axis_source == "stored" else "column index"}</text>'
    )
    out.append(
        f'<text x="{margin}" y="{plot_y + plot_h + 38}" fill="{_INK_FAINT}" font-size="11">'
        f'{"row breakpoints" if axis_source == "stored" else "row index"}</text>'
    )

    # Colour scale.
    bar_x = plot_x + plot_w + 28
    steps = 48
    for i in range(steps):
        t = i / (steps - 1)
        color = value_color(t)
        out.append(
            f'<rect x="{bar_x}" y="{plot_y + plot_h - (i + 1) * plot_h / steps:.1f}" '
            f'width="18" height="{plot_h / steps + 0.5:.2f}" fill="{_hex(color)}"/>'
        )
    out.append(
        f'<text x="{bar_x + 24}" y="{plot_y + 12}" fill="{_INK_DIM}" font-size="11">{high}</text>'
    )
    out.append(
        f'<text x="{bar_x + 24}" y="{plot_y + plot_h}" fill="{_INK_DIM}" font-size="11">{low}</text>'
    )
    out.append(
        f'<text x="{bar_x}" y="{plot_y - 8}" fill="{_INK_FAINT}" font-size="11">value</text>'
    )

    # Footer: what this picture is, and what it is not.
    footer_y = plot_y + plot_h + axis_h
    out.append(
        f'<line x1="{margin}" y1="{footer_y}" x2="{total_w - margin}" y2="{footer_y}" '
        f'stroke="{_RULE}"/>'
    )
    for index, note in enumerate(notes):
        x = margin
        y = footer_y + 22 + index * 16
        out.append(
            f'<text x="{x}" y="{y}" fill="{_INK_FAINT}" font-size="11">{escape(note)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def measured_facts(grid_summary: dict[str, Any]) -> list[str]:
    """The numbers that belong in the header, from a grid summary."""
    return [
        f"cells {grid_summary.get('count', 0)}",
        f"min {grid_summary.get('min', 0)}",
        f"max {grid_summary.get('max', 0)}",
        f"mean {float(grid_summary.get('mean') or 0):.1f}",
        f"distinct {grid_summary.get('distinct', 0)}",
    ]
