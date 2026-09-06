"""
openremap.api._io — the single shared binary-loading helper for the gate.

Every API handler that accepts a ``path`` reads it through :func:`load_binary`:
read bytes → reject empty files → decode the image (Intel HEX / S-Record /
raw) → return flat ``bytes``.  This was previously copy-pasted into ~15 API
modules; it now lives once.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api.errors import GuardError
from openremap.core.services.convert import CONTAINER_NAMES, decode_image


def load_binary(path: str) -> bytes:
    """Read *path* and return the decoded flat binary image (``bytes``).

    Raises :class:`GuardError` for an empty file or an invalid image.
    """
    return load_image(path)[0]


def load_image(path: str) -> tuple[bytes, str]:
    """Read *path* and return ``(decoded_bytes, container_name)``.

    Like :func:`load_binary` but also returns the friendly container name
    (``CONTAINER_NAMES`` for the decoded format) — used by the identify and
    analyze handlers, which report the container in their result.
    """
    raw = Path(path).read_bytes()
    if not raw:
        raise GuardError(f"Binary file '{Path(path).name}' is empty.")
    try:
        decoded = decode_image(raw)
    except ValueError as exc:
        raise GuardError(
            f"'{Path(path).name}' is not a valid binary image: {exc}"
        ) from exc
    return decoded.data, CONTAINER_NAMES.get(decoded.format, decoded.format)
