"""
openremap.core.services.recipes.patch — the atomic patch apply.

One operation: ``ECUPatcher.apply_all`` (the "phase 2" of a tune, with no
validation phases).  Pure bytes/dict in-out; the ``patch`` RPC handler (path
I/O) stays in the API gate.
"""

from __future__ import annotations

import base64

from openremap.core.serialize import to_json_safe
from openremap.core.services.recipes.patcher import ECUPatcher


def patch_binary(
    target_data: bytes,
    recipe: dict,
    target_name: str,
    recipe_name: str,
    *,
    skip_validation: bool = False,
) -> dict:
    """Atomic apply — write mb bytes. Returns the patcher report + patched bytes."""
    patcher = ECUPatcher(
        target_data=target_data,
        recipe=recipe,
        target_name=target_name,
        recipe_name=recipe_name,
        skip_validation=skip_validation,
    )
    patched = patcher.apply_all()
    report = patcher.to_dict(patched_data=patched)

    return to_json_safe(
        {
            "patched_b64": base64.b64encode(patched).decode(),
            "report": report,
        }
    )
