"""
openremap.api.commands.scan_maps — map scanning commands.

Thin gate: the bytes→dict scan cores live in
``openremap.core.services.maps.scan_maps`` (re-exported here for the CLI);
this module keeps only the path-I/O handlers and the two-stage cached pair for
interactive UIs.

Methods:

  scan_maps       — one-shot scan (axes + tables + optional classify/xref),
                    CLI ``--json``-shaped for a single file.
  scan_map_axes   — stage 1 of the two-stage scan: detect axes only (cached).
  scan_map_tables — stage 2: pair axes into tables (cached).
  scan_classify   — scan + content-label classification (atomic).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.maps.map_hunter import scan_map_axes, scan_map_tables
from openremap.core.services.maps.scan_maps import (
    build_json_result,
    classify_for_file,
    scan_maps_binary,
    scan_one,
    xref_for_file,
)

log = logging.getLogger(__name__)

# Re-export for the CLI.
__all__ = [
    "scan_one",
    "classify_for_file",
    "xref_for_file",
    "build_json_result",
    "scan_maps_binary",
    "scan_maps",
    "scan_classify",
    "scan_map_axes_cmd",
    "scan_map_tables_cmd",
]

# (path, mtime_ns, size) -> (data, axes_serialised, axes_typed)
_AXES_CACHE: dict[tuple[str, int, int], tuple[bytes, list[dict], list]] = {}
_AXES_CACHE_MAX = 4
# (path, mtime_ns, size) -> serialised tables list
_TABLES_CACHE: dict[tuple[str, int, int], list[dict]] = {}
_TABLES_CACHE_MAX = 4


def _serialise_axes(axes) -> list[dict]:
    return [
        {
            "offset": ax.offset,
            "length": ax.length,
            "byte_order": ax.byte_order,
            "values": list(ax.values[:64]),
        }
        for ax in axes
    ]


def _serialise_tables(tables) -> list[dict]:
    return [
        {
            "offset": t.offset,
            "cols": t.cols,
            "rows": t.rows,
            "cell_width": t.cell_width,
            "byte_order": t.byte_order,
            "x_axis_offset": t.x_axis_offset,
            "y_axis_offset": t.y_axis_offset,
            "stride": t.stride,
            "score": t.score,
        }
        for t in tables
    ]


def _cache_key(path: str) -> tuple[str, int, int]:
    st = os.stat(path)
    return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


@register(
    "scan_maps",
    kind="command",
    description="Scan a binary for calibration map axes and 2D tables (one-shot, CLI-shaped).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "region_start": {"type": "int", "description": "Optional region start offset."},
        "region_end": {"type": "int", "description": "Optional region end offset."},
        "min_score": {"type": "float", "default": 0.85, "min": 0.0, "max": 1.0},
        "max_series_tables": {"type": "int", "default": 16, "min": 1},
        "whole_file": {"type": "bool", "default": False},
        "top": {"type": "int", "default": 20, "min": 1},
        "classify": {"type": "bool", "default": False},
        "xrefs": {"type": "bool", "default": False},
    },
)
def scan_maps(params: dict) -> dict:
    path = params["path"]
    data = load_binary(path)

    region: slice | None = None
    if "region_start" in params and "region_end" in params:
        region = slice(int(params["region_start"]), int(params["region_end"]))

    return scan_maps_binary(
        data,
        path,
        region=region,
        min_score=float(params.get("min_score", 0.85)),
        max_series_tables=int(params.get("max_series_tables", 16)),
        layout_default=not bool(params.get("whole_file", False)),
        top=int(params.get("top", 20)),
        classify=bool(params.get("classify", False)),
        xrefs=bool(params.get("xrefs", False)),
    )


@register(
    "scan_classify",
    kind="command",
    description="Scan a binary and annotate tables with probabilistic content labels.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "min_score": {"type": "float", "default": 0.85, "min": 0.0, "max": 1.0},
        "max_series_tables": {"type": "int", "default": 16, "min": 1},
        "whole_file": {"type": "bool", "default": False},
        "top": {"type": "int", "default": 20, "min": 1},
    },
)
def scan_classify(params: dict) -> dict:
    path = params["path"]
    data = load_binary(path)
    return scan_maps_binary(
        data,
        path,
        min_score=float(params.get("min_score", 0.85)),
        max_series_tables=int(params.get("max_series_tables", 16)),
        layout_default=not bool(params.get("whole_file", False)),
        top=int(params.get("top", 20)),
        classify=True,
    )


@register(
    "scan_map_axes",
    kind="command",
    description="Stage 1 of the two-stage scan: detect axes only (cached).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "min_axis_length": {"type": "int", "default": 4},
        "max_axis_length": {"type": "int", "default": 32},
        "min_step": {"type": "int", "default": 1},
        "max_step": {"type": "int", "default": 10000},
    },
)
def scan_map_axes_cmd(params: dict) -> dict:
    path = params["path"]
    key = _cache_key(path)
    data = Path(path).read_bytes()

    axes = scan_map_axes(
        data,
        min_axis_length=int(params.get("min_axis_length", 4)),
        max_axis_length=int(params.get("max_axis_length", 32)),
        min_step=int(params.get("min_step", 1)),
        max_step=int(params.get("max_step", 10000)),
    )
    serialised = _serialise_axes(axes)

    if len(_AXES_CACHE) >= _AXES_CACHE_MAX:
        oldest = next(iter(_AXES_CACHE))
        _AXES_CACHE.pop(oldest, None)
    _AXES_CACHE[key] = (data, serialised, axes)

    return {"axes": serialised, "total": len(axes)}


@register(
    "scan_map_tables",
    kind="command",
    description="Stage 2 of the two-stage scan: pair axes into tables (cached).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "min_table_score": {"type": "float", "default": 0.55, "min": 0.0, "max": 1.0},
        "max_series_tables": {"type": "int", "default": 16, "min": 1},
    },
)
def scan_map_tables_cmd(params: dict) -> dict:
    path = params["path"]
    key = _cache_key(path)

    cached_tables = _TABLES_CACHE.get(key)
    if cached_tables is not None:
        log.info(
            "scan_map_tables: cache hit for %s (%d tables)", path, len(cached_tables)
        )
        return {"tables": cached_tables, "total_tables": len(cached_tables)}

    cached = _AXES_CACHE.get(key)
    if cached is not None:
        data, _axes_serialised, axes = cached
    else:
        data = Path(path).read_bytes()
        axes = scan_map_axes(
            data,
            min_axis_length=int(params.get("min_axis_length", 4)),
            max_axis_length=int(params.get("max_axis_length", 32)),
            min_step=int(params.get("min_step", 1)),
            max_step=int(params.get("max_step", 10000)),
        )

    tables = scan_map_tables(
        data,
        axes=axes,
        min_score=float(params.get("min_table_score", 0.55)),
        max_series_tables=int(params.get("max_series_tables", 16)),
    )

    serialised = _serialise_tables(tables)

    if len(_TABLES_CACHE) >= _TABLES_CACHE_MAX:
        oldest = next(iter(_TABLES_CACHE))
        _TABLES_CACHE.pop(oldest, None)
    _TABLES_CACHE[key] = serialised

    return {"tables": serialised, "total_tables": len(serialised)}
