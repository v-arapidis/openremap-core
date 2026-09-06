"""
openremap.api.serialize — the API gate's single JSON-serialisation point.

Every API result passes through :func:`to_json_safe` exactly once, at the
dispatch boundary, so ``Enum`` members, ``bytes``, dataclasses and tuples
never leak into a client.  The legacy ``openremap/server.py`` did this inline
in each handler and drifted; it now lives in one place.

The canonical implementation lives in ``openremap.core.serialize`` (so the
service layer can also return JSON-safe dicts without importing from the API
gate); this module re-exports it to keep the gate's public surface stable.
"""

from __future__ import annotations

from openremap.core.serialize import SerializationError, to_json_safe

__all__ = ["SerializationError", "to_json_safe"]
