"""
openremap.api.commands.scan — scan: batch-identify a directory of ECU binaries.

Thin gate: the bytes→``ScanResult`` classify core lives in
``openremap.core.manufacturers.scan`` (re-exported here for the CLI + TUI);
this module keeps only the directory-traversal handler.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api.registry import register
from openremap.core.manufacturers.scan import (
    ALL_DEST,
    DEST_CONTESTED,
    DEST_SCANNED,
    DEST_SW_MISSING,
    DEST_TRASH,
    DEST_UNKNOWN,
    ORGANIZABLE_DEST,
    VALID_EXTENSIONS,
    ScanResult,
    classify_file,
)
from openremap.core.services.convert import decode_image

# Re-export for the CLI (and, via cli.commands.scan, the TUI).
__all__ = [
    "VALID_EXTENSIONS",
    "DEST_SCANNED",
    "DEST_SW_MISSING",
    "DEST_CONTESTED",
    "DEST_UNKNOWN",
    "DEST_TRASH",
    "ALL_DEST",
    "ORGANIZABLE_DEST",
    "ScanResult",
    "classify_file",
    "scan",
]


def _scan_directory(directory: str, *, recursive: bool = False) -> dict:
    """Classify every candidate file in *directory*, returning JSON-safe results."""
    root = Path(directory)
    dest_names = set(ALL_DEST)
    if recursive:
        candidates = sorted(
            f
            for f in root.rglob("*")
            if f.is_file()
            and f.suffix.lower() in VALID_EXTENSIONS
            and f.name not in dest_names
            and not any(root / d in f.parents for d in ALL_DEST)
        )
    else:
        candidates = sorted(
            f
            for f in root.iterdir()
            if f.is_file()
            and f.suffix.lower() in VALID_EXTENSIONS
            and f.name not in dest_names
        )

    results: list[dict] = []
    errors: list[dict] = []
    for fp in candidates:
        try:
            data = decode_image(fp.read_bytes()).data
        except (OSError, ValueError) as exc:
            errors.append({"file": str(fp), "error": f"READ ERR: {exc}"})
            continue
        if len(data) == 0:
            errors.append({"file": str(fp), "error": "EMPTY file"})
            continue
        r = classify_file(data, fp.name)
        row = {
            "file": str(fp),
            "destination": r.destination,
            "detail": r.detail,
        }
        if r.extraction:
            row.update(r.extraction)
        results.append(row)

    return {
        "directory": directory,
        "files_scanned": len(candidates),
        "results": results,
        "errors": errors,
    }


@register(
    "scan",
    kind="command",
    description="Batch-scan a directory of ECU binaries through all registered extractors.",
    input_schema={
        "directory": {"type": "str", "required": True, "description": "Directory to scan."},
        "recursive": {"type": "bool", "default": False},
    },
)
def scan(params: dict) -> dict:
    return _scan_directory(
        params["directory"], recursive=bool(params.get("recursive", False))
    )
