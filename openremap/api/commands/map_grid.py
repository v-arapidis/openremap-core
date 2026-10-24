"""
openremap.api.commands.map_grid — the cells of one detected table.

Thin gate over ``services/maps/table_grid``: the caller passes the binary path and
the table descriptor that ``scan_maps`` reported, and gets the grid back so it can
be drawn. No logic here — reading cells belongs to the service.
"""

from __future__ import annotations

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.maps.table_grid import grid_summary, table_grid

__all__ = ["map_grid"]


@register(
    "map_grid",
    kind="command",
    description="Read one detected table as a grid of cell values (for previews).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "table": {"type": "dict", "required": True, "description": "Table descriptor from scan_maps."},
    },
)
def map_grid(params: dict) -> dict:
    path = params["path"]
    table = params["table"]
    try:
        grid = table_grid(load_binary(path), table)
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
    return {"table": table, "rows": grid, "summary": grid_summary(grid)}
