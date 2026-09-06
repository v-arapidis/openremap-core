"""
openremap.api.composite.diff_maps — diff calibration maps between two binaries.

Composite: ``diff_maps`` composes two map scans + axis-fingerprint matching +
cell-by-cell diff + near-match + changed-block promotion.  The whole pipeline
lives in ``openremap.core.services.maps.diff_maps`` (re-exported here for the
CLI); this module is the thin gate (path I/O).
"""

from __future__ import annotations

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.maps.diff_maps import (
    _axes_similar,
    _diff_cells,
    _group_id,
    _json_safe,
    _pearson,
    _read_axis_values,
    _read_cells,
    build_diff_json,
    diff_maps_binary,
)

# Re-export for the CLI and its tests.
__all__ = [
    "diff_maps_binary",
    "build_diff_json",
    "_group_id",
    "_read_axis_values",
    "_read_cells",
    "_axes_similar",
    "_diff_cells",
    "_json_safe",
    "_pearson",
    "diff_maps",
]


@register(
    "diff_maps",
    kind="composite",
    description="Find matching calibration maps between two binaries and diff them cell-by-cell.",
    steps=["scan_map_axes", "scan_map_tables", "match_by_axis", "diff_cells"],
    input_schema={
        "stock_path": {"type": "str", "required": True, "description": "Path to the stock binary."},
        "tuned_path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "region_start": {"type": "int", "description": "Optional region start offset."},
        "region_end": {"type": "int", "description": "Optional region end offset."},
        "min_score": {"type": "float", "default": 0.55, "min": 0.0, "max": 1.0},
        "threshold": {"type": "float", "default": 0.0},
        "top": {"type": "int", "default": 50, "min": 1},
        "whole_file": {"type": "bool", "default": False},
        "max_series_tables": {"type": "int", "default": 16, "min": 1},
        "recipe": {"type": "dict", "description": "Recipe dict to cross-reference."},
    },
)
def diff_maps(params: dict) -> dict:
    stock_path = params["stock_path"]
    tuned_path = params["tuned_path"]
    stock_data = load_binary(stock_path)
    tuned_data = load_binary(tuned_path)

    region: slice | None = None
    if "region_start" in params and "region_end" in params:
        region = slice(int(params["region_start"]), int(params["region_end"]))

    result = diff_maps_binary(
        stock_data,
        tuned_data,
        region=region,
        min_score=float(params.get("min_score", 0.55)),
        max_series_tables=int(params.get("max_series_tables", 16)),
        layout_default=not bool(params.get("whole_file", False)),
        recipe_data=params.get("recipe"),
    )

    return build_diff_json(
        stock_path,
        tuned_path,
        result,
        threshold=float(params.get("threshold", 0.0)),
        top=int(params.get("top", 50)),
    )
