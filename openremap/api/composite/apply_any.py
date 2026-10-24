"""
openremap.api.composite.apply_any — ``tune-any`` (a composite).

Same pipeline as the other apply commands with a different rule set. The core
lives in openremap.core.services.recipes.apply.engine; this module is the thin
gate (path I/O + ``GuardError`` translation).
"""

from __future__ import annotations

import json
from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.apply.engine import apply_any

__all__ = ["apply_any", "apply_any_cmd"]


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
    "apply_any",
    kind="composite",
    description=(
        "Apply a recipe with no identity requirement — shift and ambiguity relaxed."
    ),
    steps=["recipe checks", "size", "anchor search", "verify"],
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
def apply_any_cmd(params: dict) -> dict:
    path = params["path"]
    try:
        recipe, recipe_name = _load_recipe(params)
        return apply_any(
            load_binary(path),
            recipe,
            Path(path).name,
            recipe_name,
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
