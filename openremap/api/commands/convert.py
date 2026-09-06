"""
openremap.api.commands.convert — convert: normalise a binary image to flat bytes.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.convert.convert_image``.  The CLI keeps the
output-file writing (presentation).  Note: the handler reads the RAW bytes
(``convert_image`` applies the ``--format`` force itself).
"""

from __future__ import annotations

from pathlib import Path

from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.convert import convert_image

_FORMAT_CHOICES = ("auto", "ihex", "srec", "bin")

# Re-export for the CLI.
__all__ = ["convert_image", "convert"]


@register(
    "convert",
    kind="command",
    description="Normalise an ECU binary image (Intel HEX / S-Record / raw) to flat bytes.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the input file."},
        "format": {"type": "str", "default": "auto", "description": "auto | ihex | srec | bin."},
    },
)
def convert(params: dict) -> dict:
    path = params["path"]
    raw = Path(path).read_bytes()
    if not raw:
        raise GuardError(f"Binary file '{Path(path).name}' is empty.")

    fmt = params.get("format", "auto")
    if fmt not in _FORMAT_CHOICES:
        raise GuardError(
            f"--format must be one of: {', '.join(_FORMAT_CHOICES)}."
        )
    force = None if fmt == "auto" else fmt
    try:
        return convert_image(raw, force=force)
    except ValueError as exc:
        raise GuardError(
            f"'{Path(path).name}' is not a valid binary image: {exc}"
        ) from exc
