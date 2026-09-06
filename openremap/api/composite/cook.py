"""
openremap.api.composite.cook — cook: diff two binaries into a .remap recipe.

Composite: the core orchestrates several independent operations across the
recipes + maps domains (diff → map scan → annotation → region tags); it lives
in ``openremap.core.services.recipes.cook``.  This module is the thin gate
(path I/O + ``GuardError`` translation).
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.cook import _tag_regions, cook_recipe

# Re-export for the CLI.
__all__ = ["_tag_regions", "cook_recipe", "cook"]


@register(
    "cook",
    kind="composite",
    description="Diff an original and modified ECU binary into a .remap recipe.",
    steps=["diff", "scan_map_tables", "attach_maps", "tag_instruction_regions"],
    input_schema={
        "original_path": {"type": "str", "required": True, "description": "Path to the stock binary."},
        "modified_path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "context_size": {"type": "int", "default": 32, "min": 8, "max": 128},
        "require_unique": {"type": "bool", "default": True},
        "annotate_maps": {"type": "bool", "default": True},
        "description": {"type": "str"},
    },
)
def cook(params: dict) -> dict:
    original_path = params["original_path"]
    modified_path = params["modified_path"]

    try:
        return cook_recipe(
            load_binary(original_path),
            load_binary(modified_path),
            Path(original_path).name,
            Path(modified_path).name,
            context_size=int(params.get("context_size", 32)),
            require_unique=params.get("require_unique", True),
            annotate_maps=params.get("annotate_maps", True),
            description=params.get("description"),
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
