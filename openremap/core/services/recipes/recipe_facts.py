"""
Read the provenance facts of a recipe, whichever schema it is in.

Schema 5.0 keeps them under ``source`` (``filename``/``size``/``sha256``/
``identity``); 4.x kept a lean ``ecu`` block. Both shapes still exist on disk —
5.0 is what we write now, 4.x is what people have — so the readers that need
``match_key`` / ``sha256`` / ``size`` ask here instead of poking at the JSON.

The apply engine reads 5.0 only (a 4.x document is refused with a pointer to the
current commands); this module exists for the tools that audit or merge both.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["identity", "match_key", "sha256", "size"]


def _source(recipe: Mapping[str, Any]) -> Mapping[str, Any]:
    source = recipe.get("source")
    return source if isinstance(source, Mapping) else {}


def _ecu(recipe: Mapping[str, Any]) -> Mapping[str, Any]:
    ecu = recipe.get("ecu")
    return ecu if isinstance(ecu, Mapping) else {}


def match_key(recipe: Mapping[str, Any]) -> str | None:
    """The stock binary's ``match_key``, from ``source.identity`` or ``ecu``."""
    identity_block = _source(recipe).get("identity")
    if isinstance(identity_block, Mapping) and identity_block.get("match_key"):
        return str(identity_block["match_key"])
    key = _ecu(recipe).get("match_key")
    return str(key) if key else None


def sha256(recipe: Mapping[str, Any]) -> str | None:
    """The stock binary's SHA-256."""
    value = _source(recipe).get("sha256") or _ecu(recipe).get("sha256")
    return str(value) if value else None


def size(recipe: Mapping[str, Any]) -> int | None:
    """The stock binary's size."""
    value = _source(recipe).get("size")
    if value is None:
        value = _ecu(recipe).get("file_size")
    if value is None:
        metadata = recipe.get("metadata")
        if isinstance(metadata, Mapping):
            value = metadata.get("original_size")
    return int(value) if value is not None else None


def identity(recipe: Mapping[str, Any]) -> dict:
    """The stock identity block (empty when the recipe carries none)."""
    block = _source(recipe).get("identity")
    if isinstance(block, Mapping):
        return {k: v for k, v in block.items() if v is not None}
    ecu = _ecu(recipe)
    if not ecu:
        return {}
    keys = (
        "match_key",
        "manufacturer",
        "ecu_family",
        "ecu_variant",
        "software_version",
        "hardware_number",
        "oem_part_number",
        "calibration_id",
        "platform",
        "confidence",
    )
    return {k: ecu[k] for k in keys if ecu.get(k) is not None}
