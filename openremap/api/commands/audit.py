"""
openremap.api.commands.audit — audit: the three-way receipt check.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.services.recipes.audit.audit_binary``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.registry import register
from openremap.core.services.recipes.audit import audit_binary

# Re-export for the CLI.
__all__ = ["audit_binary", "audit_cmd"]


@register(
    "audit",
    kind="command",
    description="Verify that a stock binary, tuned binary, and recipe belong together.",
    input_schema={
        "stock_path": {"type": "str", "required": True, "description": "Path to the stock binary."},
        "tuned_path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict to audit."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
    },
)
def audit_cmd(params: dict) -> dict:
    stock_path = params["stock_path"]
    tuned_path = params["tuned_path"]
    return audit_binary(
        load_binary(stock_path),
        load_binary(tuned_path),
        params["recipe"],
        stock_name=Path(stock_path).name,
        tuned_name=Path(tuned_path).name,
        recipe_name=params.get("recipe_name") or "recipe.remap",
    )
