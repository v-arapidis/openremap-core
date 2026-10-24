"""
openremap.api.commands.recipe_info — what a recipe file says about itself.

Thin gate: reads the .remap JSON, then hands it to
``services/recipes/recipe_info`` (which runs the apply engine's own 5.0
reader). A recipe that fails here would fail at apply time, with the same rule
id — that is the point of reusing the reader instead of re-parsing.
"""

from __future__ import annotations

import json
from pathlib import Path

from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.apply.contract import ApplyRefused
from openremap.core.services.recipes.recipe_info import recipe_info as _recipe_info

__all__ = ["recipe_info"]


@register(
    "recipe_info",
    kind="command",
    description=(
        "Read a .remap file and report what it declares: schema, instruction count, "
        "identity floor, the files it was cooked from."
    ),
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the .remap file."},
        "include_instructions": {
            "type": "bool",
            "default": False,
            "description": "Also return one compact row per instruction (offset, size, region, flags).",
        },
    },
)
def recipe_info(params: dict) -> dict:
    path = Path(params["path"])
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GuardError(f"cannot read {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GuardError(f"{path.name} is not JSON: {exc}") from exc
    try:
        return _recipe_info(
            document,
            name=path.name,
            include_instructions=bool(params.get("include_instructions", False)),
        )
    except ApplyRefused as exc:
        raise GuardError(f"{path.name}: [{exc.check}] {exc}") from exc
