"""
openremap.api.composite.cook_forced — ``cook-forced`` (a composite).

Same producer as ``cook-strict`` with non-unique anchors accepted; the recipe
declares ``identity_floor: firmware``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.produce.builder import produce_recipe
from openremap.core.services.recipes.produce.modes import COOK_FORCED

__all__ = ["cook_forced", "produce_recipe"]


@register(
    "cook_forced",
    kind="composite",
    description=(
        "Build a schema-5.0 recipe accepting non-unique anchors (change inside "
        "blank flash/padding). Declares identity_floor: firmware."
    ),
    steps=["size guard", "identity guard", "diff + anchors", "annotate", "maps/regions", "contract"],
    input_schema={
        "stock_path": {"type": "str", "required": True, "description": "Path to the stock binary."},
        "tuned_path": {"type": "str", "required": True, "description": "Path to the tuned binary."},
        "context_size": {"type": "int", "default": 32, "min": 8, "max": 128},
        "description": {"type": "str"},
    },
)
def cook_forced(params: dict) -> dict:
    stock_path = params["stock_path"]
    tuned_path = params["tuned_path"]
    try:
        return produce_recipe(
            load_binary(stock_path),
            load_binary(tuned_path),
            Path(stock_path).name,
            Path(tuned_path).name,
            mode=COOK_FORCED,
            context_size=int(params.get("context_size", 32)),
            description=params.get("description"),
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
