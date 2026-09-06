"""
openremap.core.services.maps.scan_maps — single-file map-scan cores.

The bytes→dict map-scanning logic shared by the CLI and the API gate:

  - ``scan_one``            — run axes + table scanning on one binary (raw objects)
  - ``scan_maps_binary``    — one-shot scan returning the CLI ``--json``-shaped dict
  - ``classify_for_file``   — content-label classification for a file's tables
  - ``xref_for_file``       — code-reference resolution / score adjustment
  - ``build_json_result``   — serialise a raw scan result into the JSON shape
  - ``_calibration_spans`` / ``_apply_calibration_filter`` — layout filtering

Pure bytes/dict in-out; no file paths, no presentation.  (The two-stage cached
``scan_map_axes`` / ``scan_map_tables`` RPC handlers stay in the API gate.)
"""

from __future__ import annotations

from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import scan_map_axes, scan_map_tables


def _calibration_spans(data: bytes, tables) -> list[tuple[int, int]]:
    return [
        (r.start, r.end)
        for r in segment(data, tables=tables)
        if r.kind == "calibration"
    ]


def _apply_calibration_filter(data: bytes, result: dict) -> None:
    spans = _calibration_spans(data, result["tables"])
    if not spans:
        result["layout_filtered"] = False
        result["tables_hidden"] = 0
        return

    def _inside(offset: int) -> bool:
        return any(s <= offset < e for s, e in spans)

    before = len(result["tables"])
    result["tables"] = [t for t in result["tables"] if _inside(t.offset)]
    result["layout_filtered"] = True
    result["tables_hidden"] = before - len(result["tables"])


def scan_one(
    data: bytes,
    region: slice | None,
    min_score: float,
    max_series_tables: int,
    layout_default: bool = False,
) -> dict:
    """Run axes + table scanning on a single binary (raw MapTable objects)."""
    axes = scan_map_axes(data, region=region)
    tables = scan_map_tables(
        data, region=region, axes=axes,
        min_score=min_score, max_series_tables=max_series_tables,
    )
    top_score = max((t.score for t in tables), default=0.0)
    result = {
        "axes": axes,
        "tables": tables,
        "axes_count": len(axes),
        "tables_count": len(tables),
        "top_score": top_score,
    }
    if layout_default and region is None:
        _apply_calibration_filter(data, result)
        result["tables_count"] = len(result["tables"])
    return result


def classify_for_file(data: bytes, tables) -> dict[int, tuple[str, float]]:
    from openremap.core.services.maps.map_classifier import (
        classify_tables,
        family_fuel_type,
    )

    family: str | None = None
    try:
        from openremap.core.services.identify.identifier import identify_ecu

        family = identify_ecu(data=data, filename="<scan>").get("ecu_family")
    except Exception:
        family = None
    return classify_tables(data, tables, fuel_type=family_fuel_type(family))


def xref_for_file(data: bytes, tables) -> tuple[list, object | None]:
    from openremap.core.arch import arch_for_family
    from openremap.core.arch.detect import detect_arch
    from openremap.core.arch.refs import collect_xrefs
    from openremap.core.services.maps.layout import code_regions_from_layout, segment
    from openremap.core.services.maps.xrefs import (
        _table_spans,
        adjust_table_scores,
    )

    try:
        from openremap.core.services.identify.identifier import identify_ecu

        ident = identify_ecu(data=data, filename="<scan>")
    except Exception:
        return tables, None

    arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    if not codes:
        return tables, None
    spans = _table_spans(tables)
    if arch is None:
        xr = detect_arch(data, codes, ident.get("ecu_endian"), spans)
    else:
        xr = collect_xrefs(
            data, codes, arch, ident.get("ecu_endian"), spans=spans
        )
    if xr.status != "ok":
        return tables, xr
    return adjust_table_scores(tables, xr), xr


def build_json_result(
    file_label: str,
    data: bytes,
    result: dict,
    top: int,
    labels: dict[int, tuple[str, float]] | None = None,
    xref=None,
) -> dict:
    """Build the CLI ``--json``-shaped dict for one file."""
    from openremap.core.services.maps.xrefs import xref_evidence

    axes = result["axes"]
    tables = result["tables"]
    out = {
        "file": str(file_label),
        "file_size": len(data),
        "axes_count": result["axes_count"],
        "tables_count": result["tables_count"],
        "top_score": round(result["top_score"], 4),
        "layout_filtered": bool(result.get("layout_filtered")),
        "tables_hidden": result.get("tables_hidden", 0),
        "axes": [
            {"offset": a.offset, "length": a.length, "byte_order": a.byte_order,
             "values": list(a.values[:16])}
            for a in axes[:200]
        ],
        "tables": [
            {"offset": t.offset, "cols": t.cols, "rows": t.rows,
             "cell_width": t.cell_width, "byte_order": t.byte_order,
             "x_axis_offset": t.x_axis_offset, "y_axis_offset": t.y_axis_offset,
             "stride": t.stride, "score": t.score,
             "label": (labels or {}).get(t.offset, ("unknown", 0.0))[0],
             "label_confidence": (labels or {}).get(t.offset, ("unknown", 0.0))[1],
             "xref": xref_evidence(t, xref) if xref is not None else {}}
            for t in tables[:top]
        ],
    }
    if xref is not None:
        out["xrefs"] = {
            "status": xref.status,
            "skip_reason": xref.skip_reason,
            "arch": xref.arch,
            "base_address": xref.base_address,
            "code_bytes_scanned": xref.code_bytes_scanned,
            "insn_count": xref.insn_count,
            "reference_count": len(xref.referenced),
        }
    return out


def scan_maps_binary(
    data: bytes,
    file_label: str,
    *,
    region: slice | None = None,
    min_score: float = 0.85,
    max_series_tables: int = 16,
    layout_default: bool = True,
    top: int = 20,
    classify: bool = False,
    xrefs: bool = False,
) -> dict:
    """One-shot scan returning the CLI ``--json``-shaped dict for one file."""
    result = scan_one(
        data, region, min_score, max_series_tables, layout_default=layout_default
    )
    labels = classify_for_file(data, result["tables"]) if classify else None
    xr = None
    if xrefs:
        result["tables"], xr = xref_for_file(data, result["tables"])
        result["top_score"] = max((t.score for t in result["tables"]), default=0.0)
    return build_json_result(file_label, data, result, top, labels, xr)
