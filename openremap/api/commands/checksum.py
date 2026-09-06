"""
openremap.api.commands.checksum — checksum: detect which schemes a binary satisfies.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.checksums.report.checksum_binary``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.checksums.report import checksum_binary

# Re-export for the CLI.
__all__ = ["checksum_binary", "checksum_cmd"]


@register(
    "checksum",
    kind="command",
    description="Detect which checksum schemes a binary satisfies and their OK/STALE status.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
    },
)
def checksum_cmd(params: dict) -> dict:
    path = params["path"]
    return checksum_binary(load_binary(path), Path(path).name)
