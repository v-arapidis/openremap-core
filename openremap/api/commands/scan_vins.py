"""
openremap.api.commands.scan_vins — scan-vins: locate VIN candidates.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.identify.vin_report.scan_vins_binary``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.identify.vin_report import scan_vins_binary

# Re-export for the CLI.
__all__ = ["scan_vins_binary", "scan_vins_cmd"]


@register(
    "scan_vins",
    kind="command",
    description="Locate VIN candidates in an ECU binary and score them.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "min_confidence": {"type": "float", "default": 0.4, "min": 0.0, "max": 1.0},
    },
)
def scan_vins_cmd(params: dict) -> dict:
    path = params["path"]
    return scan_vins_binary(
        load_binary(path),
        Path(path).name,
        min_confidence=float(params.get("min_confidence", 0.4)),
    )
