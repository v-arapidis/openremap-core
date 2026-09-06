"""
openremap.api.commands.tune — the atomic patch apply.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.recipes.patch.patch_binary``.  The 3-phase safe
``tune`` lives in ``openremap.api.composite.tune`` (a composite).
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.recipes.patch import patch_binary

# Re-export for the CLI.
__all__ = ["patch_binary", "patch"]


@register(
    "patch",
    kind="command",
    description="Atomically apply a recipe to a target binary (no validation phases).",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the target binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict to apply."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
        "skip_validation": {"type": "bool", "default": False},
    },
)
def patch(params: dict) -> dict:
    path = params["path"]
    return patch_binary(
        load_binary(path),
        params["recipe"],
        Path(path).name,
        params.get("recipe_name") or "recipe.remap",
        skip_validation=bool(params.get("skip_validation", False)),
    )
