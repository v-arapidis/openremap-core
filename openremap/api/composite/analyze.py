"""
openremap.api.composite.analyze — analyze: describe a whole ECU binary.

Composite: ``analyze`` composes identity + VIN + flash layout + maps +
checksums + health + coherence into one combined report.  The heavy lifting
lives in ``core.services.analyze.analyze_binary``; this module is the thin
gate (container detection + ``.to_dict()`` serialisation).
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_image
from openremap.api.registry import register
from openremap.core.services.analyze import (
    AnalyzeReport,
    analyze_binary,
    analyze_binary_dict,
)

# Re-export for the CLI (the rich ``analyze_binary`` + ``AnalyzeReport`` for
# the human renderer; ``analyze_binary_dict`` is the JSON-safe core).
__all__ = ["analyze_binary", "AnalyzeReport", "analyze_binary_dict", "analyze"]


@register(
    "analyze",
    kind="composite",
    description="Describe a whole ECU binary: identity, VIN, layout, maps, checksums, health.",
    steps=["identify", "scan_map_axes", "scan_map_tables", "check_coherence", "health"],
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "fast": {"type": "bool", "default": False, "description": "Skip maps, checksums, and health."},
        "skip_maps": {"type": "bool", "default": False, "description": "Skip the map scan only."},
        "xref_mode": {"type": "str", "default": "v1", "description": "Code-reference pipeline: 'v1' or 'v2'."},
    },
)
def analyze(params: dict) -> dict:
    path = params["path"]
    data, container = load_image(path)
    return analyze_binary_dict(
        data,
        Path(path).name,
        fast=bool(params.get("fast", False)),
        skip_maps=bool(params.get("skip_maps", False)),
        container=container,
        xref_mode=str(params.get("xref_mode", "v1")),
    )
