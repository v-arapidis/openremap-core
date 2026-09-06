"""
openremap.core.serialize — the canonical JSON-serialisation helper.

Pure, domain-free conversion of a value into JSON-serialisable data.  This is
the ONE implementation of the conversion; the API gate's ``openremap.api.serialize``
re-exports it so every consumer still imports from the same surface.  Service
functions return JSON-safe dicts as their contract, so they import this from
``core`` (no ``openremap.api`` dependency — services stay below the gate).

Unknown types raise :class:`SerializationError` instead of silently
stringifying, so a missed conversion is caught during development rather than
shipping a ``"<object at 0x…>"`` string to a client.
"""

from __future__ import annotations

import base64
import enum
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class SerializationError(TypeError):
    """A value could not be converted to a JSON-serialisable form."""


def to_json_safe(value: Any) -> Any:
    """Recursively convert *value* into JSON-serialisable data.

    Conversions:
      - ``Enum`` member            → member ``name``
      - ``bytes`` / ``bytearray``  → base64 string
      - ``Path``                   → ``str``
      - dataclass instance         → ``dict`` (recursively)
      - dict keys                  → coerced to ``str``
      - list / tuple / set         → ``list`` (recursively)
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_json_safe(to_dict())

    raise SerializationError(
        f"cannot serialise {type(value).__name__!r} to JSON; "
        "add a to_dict() method or extend openremap.core.serialize"
    )
