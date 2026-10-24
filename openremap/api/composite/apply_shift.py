"""
openremap.api.composite.apply_shift — ``tune-shift`` (a composite).

Same pipeline as ``apply_strict`` with one rule relaxed (a displaced anchor is
allowed) and one tightened (a non-unique anchor is refused). The core lives in
``openremap.core.services.recipes.apply.engine``; this module is the thin gate
(path I/O + ``GuardError`` translation) — exactly like ``apply_strict``.
"""

from __future__ import annotations

import json
from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.apply.engine import apply_shift

__all__ = ["apply_shift", "apply_shift_cmd"]


def _load_recipe(params: dict) -> tuple[dict, str]:
    """The recipe dict and its name — from a path or from the caller."""
    path = params.get("recipe_path")
    if path:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return document, params.get("recipe_name") or Path(path).name
    if "recipe" not in params:
        raise GuardError("give either 'recipe' (a dict) or 'recipe_path' (a file)")
    return params["recipe"], params.get("recipe_name") or "recipe.remap"


@register(
    "apply_shift",
    kind="composite",
    description=(
        "Apply a recipe when the target is the same firmware but the change is "
        "found at a displaced offset (±2048 bytes). Shift is reported; a "
        "non-unique anchor is refused."
    ),
    steps=["recipe checks", "identity gate", "anchor search", "shift report", "verify"],
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the target binary."},
        "recipe": {"type": "dict", "description": "Recipe dict to apply (or use recipe_path)."},
        "recipe_path": {
            "type": "str",
            "description": "Path to a .remap file to read and apply (alternative to recipe).",
        },
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
    },
)
def apply_shift_cmd(params: dict) -> dict:
    path = params["path"]
    try:
        recipe, recipe_name = _load_recipe(params)
        return apply_shift(
            load_binary(path),
            recipe,
            Path(path).name,
            recipe_name,
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
