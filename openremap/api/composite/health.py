"""
openremap.api.composite.health — health: one-shot calibration health check.

Composite: ``health`` composes identity + checksums + axis sanity + map-count
envelope + erased-block layout + VIN duplication into one verdict.  The heavy
lifting lives in ``core.services.health.health_report``; this module is the
thin gate.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.health import health_binary, health_payload

# Re-export for the CLI.
__all__ = ["health_payload", "health_binary", "health"]


@register(
    "health",
    kind="composite",
    description="One-shot calibration health check (identity, checksums, maps, layout, VIN).",
    steps=["identify", "checksums", "scan_map_tables", "segment", "scan_vins"],
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
    },
)
def health(params: dict) -> dict:
    path = params["path"]
    return health_binary(load_binary(path), Path(path).name)
