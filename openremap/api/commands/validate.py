"""
openremap.api.commands.validate — validate a binary against a recipe.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.recipes.validate``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.recipes.validate import (
    validate_after_binary,
    validate_before_binary,
    validate_check_binary,
)

# Re-export for the CLI.
__all__ = [
    "validate_before_binary",
    "validate_check_binary",
    "validate_after_binary",
    "validate_before",
    "validate_check",
    "validate_after",
]


def _recipe_name(params: dict) -> str:
    return params.get("recipe_name") or "recipe.remap"


@register(
    "validate_before",
    kind="command",
    description="Pre-flight strict check — verify ob bytes are at recorded offsets.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the untuned binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict to validate against."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
    },
)
def validate_before(params: dict) -> dict:
    path = params["path"]
    return validate_before_binary(
        load_binary(path), params["recipe"], Path(path).name, _recipe_name(params)
    )


@register(
    "validate_check",
    kind="command",
    description="Diagnostic — search the whole binary for the recipe's ob bytes.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the target binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict to validate against."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
    },
)
def validate_check(params: dict) -> dict:
    path = params["path"]
    return validate_check_binary(
        load_binary(path), params["recipe"], Path(path).name, _recipe_name(params)
    )


@register(
    "validate_after",
    kind="command",
    description="Post-tune confirmation — verify mb bytes were written correctly.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict used during tuning."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
    },
)
def validate_after(params: dict) -> dict:
    path = params["path"]
    return validate_after_binary(
        load_binary(path), params["recipe"], Path(path).name, _recipe_name(params)
    )
