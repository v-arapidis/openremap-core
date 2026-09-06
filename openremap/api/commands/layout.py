"""
openremap.api.commands.layout — layout: flash-layout segmentation.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.maps.layout.layout_binary``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.maps.layout import layout_binary

# Re-export for the CLI.
__all__ = ["layout_binary", "layout"]


@register(
    "layout",
    kind="command",
    description="Segment an ECU binary into its flash-layout blocks.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "min_run": {"type": "int", "default": 64, "min": 1},
    },
)
def layout(params: dict) -> dict:
    path = params["path"]
    return layout_binary(
        load_binary(path),
        Path(path).name,
        min_run=int(params.get("min_run", 64)),
    )
