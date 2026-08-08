"""
openremap.server — JSON-RPC daemon for OpenRemap Studio.

Transport : line-delimited JSON (NDJSON) over stdin/stdout.
Protocol  : one JSON object per line, no batching.
Logging   : stderr only — stdout is exclusively for JSON-RPC responses.

Request  → {"id": <int>, "method": "<name>", "params": {...}}
Response ← {"id": <int>, "result": {...}}
         | {"id": <int>, "error":  "<message>"}

Spawn via:
    python -m openremap.server
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging — stderr only so stdout stays clean for JSON-RPC.
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[openremap-server] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handlers
# Heavy imports are deferred inside each handler: startup stays instant, and
# only the modules actually needed get loaded.
# ---------------------------------------------------------------------------


def _ping(params: dict) -> dict:
    return {"ok": True}


def _version(params: dict) -> dict:
    from openremap import __version__

    return {"version": __version__}


def _infer_platform(ecu_family: str | None) -> str | None:
    """Infer processor architecture from ECU family name."""
    if not ecu_family:
        return None
    fam = ecu_family.upper()
    if any(
        x in fam
        for x in [
            "EDC17",
            "MEDC17",
            "MED17",
            "ME17",
            "MED9",
            "MD1",
            "SIMOS",
            "SID",
            "PPD",
        ]
    ):
        return "TriCore"
    if any(
        x in fam
        for x in [
            "ME7",
            "ME9",
            "ME1",
            "M5.",
            "M4.",
            "M3.",
            "M2.",
            "M1.",
            "MP",
            "EDC16",
            "EDC15",
            "LH-",
            "MONO",
            "DME",
            "EZK",
            "KE-",
        ]
    ):
        return "C167/M8"
    if any(x in fam for x in ["EDC3", "EDC1"]):
        return "68K"
    if any(x in fam for x in ["IAW", "MJD"]):
        return "ST10"
    if any(x in fam for x in ["MULTEC", "DELPHI"]):
        return "HC12"
    return None


def _identify(params: dict) -> dict:
    from openremap.core.services.confidence import score_identity
    from openremap.core.services.identifier import identify_ecu

    path = params["path"]
    data = Path(path).read_bytes()
    filename = Path(path).name

    identity = identify_ecu(data, filename)
    confidence = score_identity(identity, filename)

    # detection_strength may be an Enum — normalise to the member name or None.
    det_strength = identity.get("detection_strength")
    if det_strength is not None:
        ds = str(det_strength)
        det_strength = ds.rsplit(".", 1)[-1] if "." in ds else ds

    return {
        "manufacturer": identity.get("manufacturer"),
        "match_key": identity.get("match_key"),
        "ecu_family": identity.get("ecu_family"),
        "ecu_variant": identity.get("ecu_variant"),
        "software_version": identity.get("software_version"),
        "hardware_number": identity.get("hardware_number"),
        "calibration_id": identity.get("calibration_id"),
        "oem_part_number": identity.get("oem_part_number"),
        "detection_strength": det_strength,
        "detection_evidence": list(identity.get("detection_evidence", [])),
        "file_size": identity.get("file_size", 0),
        "sha256": identity.get("sha256", ""),
        "confidence": {
            "score": confidence.score,
            "tier": confidence.tier,
            "signals": [
                {"delta": s.delta, "label": s.label} for s in confidence.signals
            ],
            "warnings": list(confidence.warnings),
        },
        "md5": identity.get("md5", ""),
        "calibration_version": identity.get("calibration_version"),
        "sw_base_version": identity.get("sw_base_version"),
        "serial_number": identity.get("serial_number"),
        "dataset_number": identity.get("dataset_number"),
        "raw_strings": list(identity.get("raw_strings") or [])[:50],
        "platform": _infer_platform(identity.get("ecu_family")),
        "ecu_endian": identity.get("ecu_endian", "little"),
        "ecu_cell_bytes": identity.get("ecu_cell_bytes", 2),
    }


def _cook(params: dict) -> dict:
    from openremap.core.services.recipe_builder import ECUDiffAnalyzer

    original_path = params["original_path"]
    modified_path = params["modified_path"]
    context_size = int(params.get("context_size", 32))
    description = params.get("description")
    require_unique = params.get("require_unique", True)

    original_data = Path(original_path).read_bytes()
    modified_data = Path(modified_path).read_bytes()

    analyzer = ECUDiffAnalyzer(
        original_data,
        modified_data,
        Path(original_path).name,
        Path(modified_path).name,
        context_size=context_size,
        require_unique=require_unique,
    )

    size_warn = analyzer.check_size_match()
    if size_warn:
        raise ValueError(f"Size mismatch: {size_warn}")

    # Run the full diff + guard checks via build_recipe().
    # This populates warnings (identity mismatch, non-unique anchors) and
    # runs the annotator on the recipe instructions.
    recipe = analyzer.build_recipe(description=description)
    stats = analyzer.compute_stats()
    warnings = analyzer.cook_warnings()

    # When tune metadata is provided, also build the .orst 2.0 payload
    # for the save pipeline (GPUI Ctrl+S / editor close).
    tune_id = params.get("tune_id")
    if tune_id:
        orst = analyzer.build_orst(
            id=tune_id,
            name=params.get("tune_name", ""),
            message=params.get("tune_message"),
            source_sha256=params.get("source_sha256", ""),
            source_path_hint=params.get("source_path_hint", ""),
            base_tune_id=params.get("base_tune_id"),
            created_at=params.get("tune_created_at"),
            modified_at=params.get("tune_modified_at"),
        )
        return {"orst": orst, "stats": stats, "warnings": warnings}

    # Legacy path — full recipe for callers that don't pass tune metadata.
    return {"recipe": recipe, "stats": stats, "warnings": warnings}


def _tune(params: dict) -> dict:
    from openremap.core.services.patcher import ECUPatcher

    path = params["path"]
    recipe = params["recipe"]
    output_path: str | None = params.get("output_path")
    skip_validation: bool = bool(params.get("skip_validation", False))

    target_data = Path(path).read_bytes()

    patcher = ECUPatcher(
        target_data,
        recipe,
        target_name=Path(path).name,
        skip_validation=skip_validation,
    )

    warnings = patcher.preflight_warnings()
    patched = patcher.apply_all()
    total, success, failed = patcher.score()
    result = patcher.to_dict(patched)

    if output_path:
        Path(output_path).write_bytes(patched)
        result["output_path"] = output_path
    else:
        # Caller didn't specify an output file — return bytes as base64.
        result["patched_b64"] = base64.b64encode(patched).decode()

    result["preflight_warnings"] = warnings
    result["patch_total"] = total
    result["patch_success"] = success
    result["patch_failed"] = failed
    return result


def _validate(params: dict) -> dict:
    from openremap.core.services.validate_exists import ECUExistenceValidator

    path = params["path"]
    recipe = params["recipe"]

    target_data = Path(path).read_bytes()

    validator = ECUExistenceValidator(
        target_data,
        recipe,
        target_name=Path(path).name,
    )
    validator.validate_all()

    exact, shifted, missing = validator.counts()

    return {
        "verdict": validator.verdict(),
        "counts": {"exact": exact, "shifted": shifted, "missing": missing},
        "size_warning": validator.check_file_size(),
        "match_key_warning": validator.check_match_key(),
        "details": validator.to_dict(),
    }


def _scan_maps(params: dict) -> dict:
    from openremap.core.services.map_hunter import scan_map_axes, scan_map_tables

    path = params["path"]
    data = Path(path).read_bytes()

    region: slice | None = None
    if "region_start" in params and "region_end" in params:
        region = slice(int(params["region_start"]), int(params["region_end"]))

    axes = scan_map_axes(
        data,
        region=region,
        min_axis_length=int(params.get("min_axis_length", 4)),
        max_axis_length=int(params.get("max_axis_length", 32)),
        min_step=int(params.get("min_step", 1)),
        max_step=int(params.get("max_step", 10000)),
    )

    # Promote axis pairs into table candidates.  Pass the axes we already
    # have to avoid a second scan.  Honours the same region slice so the
    # offsets line up with the axis offsets we return.
    buf = data[region] if region is not None else data
    tables = scan_map_tables(
        buf,
        axes=axes,
        min_score=float(params.get("min_table_score", 0.55)),
    )

    return {
        "axes": _serialise_axes(axes),
        "tables": _serialise_tables(tables),
        "total": len(axes),
        "total_tables": len(tables),
    }


# ---------------------------------------------------------------------------
# Two-stage map scan: axes first (fast, populates UI), then tables (slow).
#
# Studio uses these instead of `scan_maps` so the Maps panel can show
# axes while table pairing is still in flight.  We keep a small LRU of
# the most recently scanned files so the second call doesn't have to
# re-read the file from disk or re-run `scan_map_axes`.
# ---------------------------------------------------------------------------

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
            # Truncate very long value tuples so the JSON stays reasonable.
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
            "score": t.score,
        }
        for t in tables
    ]


def _cache_key(path: str) -> tuple[str, int, int]:
    st = os.stat(path)
    return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


def _scan_map_axes_only(params: dict) -> dict:
    """Stage 1 of the two-stage scan: detect axes only.

    The returned axes are cached (keyed by path + mtime + size) so the
    follow-up `scan_map_tables` call doesn't have to re-do the work.
    """
    from openremap.core.services.map_hunter import scan_map_axes

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

    # Trim cache, then insert.
    if len(_AXES_CACHE) >= _AXES_CACHE_MAX:
        # FIFO eviction of the oldest entry is fine for a 4-slot cache.
        oldest = next(iter(_AXES_CACHE))
        _AXES_CACHE.pop(oldest, None)
    _AXES_CACHE[key] = (data, serialised, axes)

    return {
        "axes": serialised,
        "total": len(axes),
    }


def _scan_map_tables_only(params: dict) -> dict:
    """Stage 2 of the two-stage scan: pair axes into tables.

    Reuses the cached buffer + axes from a prior `scan_map_axes` call
    when the file hasn't changed; otherwise re-reads and re-scans.
    Result is cached by (path, mtime, size) so revisiting the same file
    skips the expensive O(N\u00b2) pairing pass entirely.
    """
    from openremap.core.services.map_hunter import scan_map_axes, scan_map_tables

    path = params["path"]
    key = _cache_key(path)

    # Fast path: return cached result when the file is unchanged.
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
    )

    serialised = _serialise_tables(tables)

    # Store in tables cache (FIFO eviction).
    if len(_TABLES_CACHE) >= _TABLES_CACHE_MAX:
        oldest = next(iter(_TABLES_CACHE))
        _TABLES_CACHE.pop(oldest, None)
    _TABLES_CACHE[key] = serialised

    return {
        "tables": serialised,
        "total_tables": len(serialised),
    }


def _scan(params: dict) -> dict:
    """Batch-identify a list of file paths."""
    from openremap.core.services.confidence import score_identity
    from openremap.core.services.identifier import identify_ecu

    results = []
    for p in params.get("paths", []):
        try:
            data = Path(p).read_bytes()
            filename = Path(p).name
            identity = identify_ecu(data, filename)
            confidence = score_identity(identity, filename)
            results.append(
                {
                    "path": p,
                    "manufacturer": identity.get("manufacturer"),
                    "ecu_family": identity.get("ecu_family"),
                    "software_version": identity.get("software_version"),
                    "confidence_tier": confidence.tier,
                    "confidence_score": confidence.score,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append({"path": p, "error": str(exc)})

    return {"results": results}


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "ping": _ping,
    "version": _version,
    "identify": _identify,
    "cook": _cook,
    "tune": _tune,
    "validate": _validate,
    "scan_maps": _scan_maps,
    "scan_map_axes": _scan_map_axes_only,
    "scan_map_tables": _scan_map_tables_only,
    "scan": _scan,
}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("openremap-server started (pid=%d)", os.getpid())

    # Restore the default SIGPIPE handler so that a broken stdout pipe
    # terminates the process cleanly instead of raising an unhandled
    # Python exception. (SIGPIPE is POSIX-only; silently skip on Windows.)
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, OSError):
        pass

    # Run at lower OS priority so the background scan doesn't compete
    # with the UI process for CPU time.  The scan still gets full
    # throughput when the system is idle — it just backs off under load.
    # nice() is POSIX-only; silently skip on Windows.
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue

        req_id: int | None = None
        method = "?"

        try:
            req = json.loads(raw)
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params") or {}

            handler = HANDLERS.get(method)
            if handler is None:
                raise ValueError(f"Unknown method: {method!r}")

            result = handler(params)
            response: dict = {"id": req_id, "result": result}

        except ValueError as exc:
            # Guard rejection (e.g. non-unique anchors) — expected, not a bug.
            # The error message goes to the client via JSON-RPC; no traceback needed.
            log.warning("%s method=%s: %s", method, req_id, exc)
            response = {"id": req_id, "error": str(exc)}
        except Exception as exc:
            log.exception("Unexpected error handling request id=%s method=%s", req_id, method)
            response = {"id": req_id, "error": str(exc)}

        try:
            print(json.dumps(response), flush=True)
        except BrokenPipeError:
            # The Rust client closed its end of the pipe (e.g. the app
            # exited while we were still processing a request). There is
            # nothing left to write to — exit the loop cleanly.
            log.info("openremap-server: stdout pipe broken — client disconnected.")
            break

    log.info("openremap-server: stdin closed — shutting down.")


if __name__ == "__main__":
    main()
