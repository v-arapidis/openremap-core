"""
openremap.api.commands.identify — identify: ECU identity + confidence + VIN.

Thin gate: path I/O + container detection here; the bytes→dict logic lives in
``openremap.core.services.identify.identify_binary``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_image
from openremap.api.registry import register
from openremap.core.services.identify.identify_binary import identify_binary

# Re-export for the CLI (which passes its own decoded bytes + container name).
__all__ = ["identify_binary", "identify"]


@register(
    "identify",
    kind="command",
    description="Identify an ECU binary — manufacturer, family, confidence, VIN.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
    },
)
def identify(params: dict) -> dict:
    path = params["path"]
    data, container = load_image(path)
    return identify_binary(data, Path(path).name, container)
