"""
openremap.api.commands.merge — merge: combine two recipes into one.

Thin gate: the merge logic lives in
``openremap.core.services.recipes.recipe_merge`` (``merge_recipes`` /
``merge_recipes_dict``); the gate translates ``MergeConflict`` into
``GuardError``.
"""

from __future__ import annotations

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.recipe_merge import (
    MergeConflict,
    merge_recipes_dict,
)

# Re-export for the CLI.
__all__ = ["merge_recipes_dict", "merge"]


@register(
    "merge",
    kind="command",
    description="Merge two recipes built from the same family of originals into one.",
    input_schema={
        "recipe_a": {"type": "dict", "required": True, "description": "First recipe dict."},
        "recipe_b": {"type": "dict", "required": True, "description": "Second recipe dict."},
        "name_a": {"type": "str", "description": "First recipe's display name."},
        "name_b": {"type": "str", "description": "Second recipe's display name."},
        "stock_path": {"type": "str", "description": "Common stock binary path (merge base)."},
        "strict": {"type": "bool", "default": False, "description": "Abort instead of skipping mismatched instructions."},
    },
)
def merge(params: dict) -> dict:
    stock_data = None
    if params.get("stock_path"):
        stock_data = load_binary(params["stock_path"])

    try:
        return merge_recipes_dict(
            params["recipe_a"],
            params["recipe_b"],
            name_a=params.get("name_a") or "a.remap",
            name_b=params.get("name_b") or "b.remap",
            stock_data=stock_data,
            strict=bool(params.get("strict", False)),
        )
    except MergeConflict as exc:
        raise GuardError(str(exc)) from exc
