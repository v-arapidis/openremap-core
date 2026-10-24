"""
openremap.api.commands.scan — scan: batch-identify a directory of ECU binaries.

Thin gate: the bytes→``ScanResult`` classify core lives in
``openremap.core.manufacturers.scan`` (re-exported here for the CLI + TUI);
this module keeps only the directory-traversal handler.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api.errors import GuardError
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
from openremap.core.services.scan_organize import (
    apply_organize,
    plan_organize,
    scan_candidates,
    skipped_files,
)

# Re-export for the CLI (and, via cli.commands.scan, the TUI).
# Grouped constants first, then callables — deliberately not alphabetical.
__all__ = [  # noqa: RUF022
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
    "scan_candidates_cmd",
    "classify_file_cmd",
    "organize_preview",
    "organize_apply",
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


def _row_for(path: Path, data: bytes) -> dict:
    """One classified file, JSON-safe (the same shape ``scan`` emits)."""
    result = classify_file(data, path.name)
    row: dict = {
        "file": str(path),
        "destination": result.destination,
        "detail": result.detail,
    }
    if result.extraction:
        row.update(result.extraction)
    return row


@register(
    "scan_candidates",
    kind="command",
    description="List the files a batch scan would classify in a folder (no work done).",
    input_schema={
        "directory": {"type": "str", "required": True, "description": "Folder to list."},
        "recursive": {"type": "bool", "default": False},
    },
)
def scan_candidates_cmd(params: dict) -> dict:
    root = Path(params["directory"])
    candidates = scan_candidates(root, recursive=bool(params.get("recursive", False)))
    return {
        "directory": str(root),
        "files": [str(path) for path in candidates],
        "count": len(candidates),
        "skipped": skipped_files(root, recursive=bool(params.get("recursive", False))),
    }


@register(
    "classify_file",
    kind="command",
    description="Classify one binary with every registered extractor (a batch step).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
    },
)
def classify_file_cmd(params: dict) -> dict:
    path = Path(params["path"])
    try:
        data = decode_image(path.read_bytes()).data
    except (OSError, ValueError) as exc:
        raise GuardError(f"{path.name}: {exc}") from exc
    if not data:
        raise GuardError(f"{path.name}: empty file")
    return _row_for(path, data)


@register(
    "organize_preview",
    kind="command",
    description="Where a scan's results would be filed, without touching anything.",
    input_schema={
        "directory": {"type": "str", "required": True, "description": "The scanned folder."},
        "rows": {
            "type": "list",
            "required": True,
            "description": "Scan rows (file + destination + identity).",
        },
        "nested": {"type": "bool", "default": True},
    },
)
def organize_preview(params: dict) -> dict:
    root = Path(params["directory"])
    plan = plan_organize(root, params["rows"], nested=bool(params.get("nested", True)))
    return {
        "count": plan.count,
        "folders": plan.relative_folders(),
        "files": [
            {"source": str(item.source), "destination": str(item.destination)}
            for item in plan.files
        ],
    }


@register(
    "organize_apply",
    kind="command",
    description="Copy (default) or move a scan's files into their folders.",
    input_schema={
        "directory": {"type": "str", "required": True, "description": "The scanned folder."},
        "rows": {"type": "list", "required": True, "description": "Scan rows to file."},
        "nested": {"type": "bool", "default": True},
        "move": {"type": "bool", "default": False, "description": "Move instead of copy."},
    },
)
def organize_apply(params: dict) -> dict:
    root = Path(params["directory"])
    plan = plan_organize(root, params["rows"], nested=bool(params.get("nested", True)))
    outcome = apply_organize(plan.files, move=bool(params.get("move", False)))
    return outcome.as_dict()
