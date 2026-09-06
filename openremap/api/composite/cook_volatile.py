"""
openremap.api.composite.cook_volatile — cook a car-portable recipe.

Composite: ``cook_volatile`` composes the ``cook`` pipeline (diff → map scan →
annotation → region tags) with a volatile-classification pass
(``classify_volatile``) and the exclusion filter.  The core lives in
``openremap.core.services.recipes.cook_volatile``; this module is the thin
gate (path I/O + ``GuardError`` translation).
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.cook_volatile import cook_volatile_recipe

# Re-export for the CLI.
__all__ = ["cook_volatile_recipe", "cook_volatile"]


@register(
    "cook_volatile",
    kind="composite",
    description="Cook a car-portable recipe (volatile instructions classified + excluded).",
    steps=["diff", "classify_volatile", "scan_map_tables", "attach_maps", "tag_instruction_regions"],
    input_schema={
        "original_path": {"type": "str", "required": True, "description": "Path to the stock binary."},
        "modified_path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "context_size": {"type": "int", "default": 32, "min": 8, "max": 128},
        "require_unique": {"type": "bool", "default": True},
        "no_exclude": {"type": "bool", "default": False},
        "exclude_uncertain": {"type": "bool", "default": False},
        "annotate_maps": {"type": "bool", "default": True},
    },
)
def cook_volatile(params: dict) -> dict:
    original_path = params["original_path"]
    modified_path = params["modified_path"]
    try:
        return cook_volatile_recipe(
            load_binary(original_path),
            load_binary(modified_path),
            Path(original_path).name,
            Path(modified_path).name,
            context_size=int(params.get("context_size", 32)),
            require_unique=params.get("require_unique", True),
            no_exclude=params.get("no_exclude", False),
            exclude_uncertain=params.get("exclude_uncertain", False),
            annotate_maps=params.get("annotate_maps", True),
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
