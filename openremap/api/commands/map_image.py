"""
openremap.api.commands.map_image — one detected table as an SVG picture.

Thin gate over ``services/maps/map_svg`` + ``services/maps/table_grid``: the
caller passes the binary path and the table descriptor ``scan_maps`` reported,
and gets the SVG document back as a string. Writing the file is the caller's job
(the CLI writes next to the dump, a GUI would embed it), so the gate still does
no I/O beyond reading the binary.
"""

from __future__ import annotations

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.maps.map_svg import render_map_svg
from openremap.core.services.maps.table_grid import grid_summary, read_axis, table_grid

__all__ = ["map_image"]


def _axes(data: bytes, table: dict) -> tuple[list[int] | None, list[int] | None, str]:
    """Breakpoints: the caller's if it has them, else the ones stored in the binary.

    A caller that already has the axes (``scan_maps`` reports them, and the DIFF
    screen reads them from the diff) must not have them looked up a second time
    from a descriptor that may not carry the offsets: the picture would silently
    fall back to cell indices while the caller shows real breakpoints next to it.
    """
    given_x, given_y = table.get("x_axis"), table.get("y_axis")
    if given_x and given_y:
        return (
            [int(value) for value in given_x],
            [int(value) for value in given_y],
            "stored",
        )
    cols, rows = int(table.get("cols") or 0), int(table.get("rows") or 0)
    width = int(table.get("cell_width") or 2)
    order = str(table.get("byte_order") or "little")
    x_axis = read_axis(
        data, int(table.get("x_axis_offset") or 0), cols, cell_width=width, byte_order=order
    )
    y_axis = read_axis(
        data, int(table.get("y_axis_offset") or 0), rows, cell_width=width, byte_order=order
    )
    source = "stored" if (x_axis and y_axis) else "index"
    return x_axis, y_axis, source


@register(
    "map_image",
    kind="command",
    description="Render one detected table as an SVG picture (values + axes + legend).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "table": {"type": "dict", "required": True, "description": "Table descriptor from scan_maps."},
        "include_values": {
            "type": "bool",
            "description": "Write each cell's number in the picture (default: for tables up to 24x24).",
        },
        "width": {"type": "int", "default": 960, "min": 400},
    },
)
def map_image(params: dict) -> dict:
    path = params["path"]
    table = params["table"]
    try:
        data = load_binary(path)
        cells = table_grid(data, table)
    except (ValueError, IndexError) as exc:
        raise GuardError(str(exc)) from exc
    if not cells:
        raise GuardError("the table has no cells to draw")

    summary = grid_summary(cells)
    x_axis, y_axis, source = _axes(data, table)
    label = table.get("label") or "unknown"
    confidence = float(table.get("label_confidence") or 0.0)
    order = "LE" if str(table.get("byte_order")) == "little" else "BE"
    shape = f"{table.get('cols')} x {table.get('rows')} cells \u00b7 {table.get('cell_width')} B {order}"
    score = f"score {float(table.get('score') or 0):.2f}"
    label_fact = f"label {label}" + (f" ({confidence:.2f})" if confidence else "")
    spread = (
        f"min {summary['min']} \u00b7 max {summary['max']} \u00b7 "
        f"mean {float(summary['mean']):.1f} \u00b7 distinct {summary['distinct']}"
    )
    facts = [f"{shape} \u00b7 {score}", label_fact, spread]
    axis_note = (
        "Axes are the breakpoints stored in the binary."
        if source == "stored"
        else "No stored breakpoints were read, so the axes are cell indices."
    )
    notes = [
        "Colours show the values stored in this binary; nothing is interpolated between cells.",
        "Labels are structural guesses, not catalog names, unless they say otherwise.",
        axis_note,
    ]
    svg = render_map_svg(
        cells,
        title=f"{params.get('name') or path}  ·  0x{int(table.get('offset') or 0):06X}",
        facts=facts,
        notes=notes,
        x_axis=x_axis,
        y_axis=y_axis,
        axis_source=source,
        include_values=params.get("include_values"),
        width=int(params.get("width") or 960),
    )
    return {
        "svg": svg,
        "table": table,
        "summary": summary,
        "axes": {"x": x_axis, "y": y_axis, "source": source},
        "label": label,
        "label_confidence": confidence,
    }
