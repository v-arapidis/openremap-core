"""
openremap.api.commands.hex_region — one byte range of a binary, as hex.

Thin gate over ``api._io.load_binary``: slice, hexadecimal, return. It exists
because the TUI must never open a file itself, and the DIFF screen has to show
the bytes of a changed region that no table accounts for. The decoding is
deliberately left to the caller — this returns the range, not a picture of it.
"""

from __future__ import annotations

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register

__all__ = ["hex_region"]

#: A single call is a *window*, not a download: the caller scrolls by asking
#: again. Bounded so a wrong number cannot put a 4 MB string in a JSON message.
MAX_LENGTH = 0x4000


@register(
    "hex_region",
    kind="command",
    description="Return a byte range of a binary as lowercase hex.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "offset": {"type": "int", "required": True, "min": 0},
        "length": {
            "type": "int",
            "required": True,
            "min": 1,
            "max": MAX_LENGTH,
            "description": f"Bytes to read (max {MAX_LENGTH}).",
        },
    },
)
def hex_region(params: dict) -> dict:
    data = load_binary(params["path"])
    offset = int(params["offset"])
    length = int(params["length"])
    if offset >= len(data):
        raise GuardError(
            f"offset 0x{offset:X} is past the end of the file ({len(data):,} bytes)"
        )
    chunk = data[offset : offset + length]
    return {
        "offset": offset,
        "requested": length,
        "length": len(chunk),
        "hex": chunk.hex(),
    }
