"""
openremap.api.composite.tune — the 3-phase safe apply (a composite).

Composite: ``tune`` orchestrates three operations that are themselves exposed
as separate API commands — ``validate_before`` → ``patch`` → ``validate_after``
— plus the same-file-only policy gate, with fast-fail on each phase.  The core
lives in ``openremap.core.services.recipes.tune``; this module is the thin
gate (path I/O + ``GuardError`` translation).  The atomic ``patch`` stays in
``openremap.api.commands.tune``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.services.recipes.tune import tune_binary

# Re-export for the CLI.
__all__ = ["tune_binary", "tune"]


@register(
    "tune",
    kind="composite",
    description="Safe 3-phase tune: validate before → patch → validate after.",
    steps=["validate_before", "patch", "validate_after"],
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the target binary."},
        "recipe": {"type": "dict", "required": True, "description": "Recipe dict to apply."},
        "recipe_name": {"type": "str", "description": "Recipe name for the report."},
        "skip_validation": {"type": "bool", "default": False},
        "force": {"type": "bool", "default": False},
    },
)
def tune(params: dict) -> dict:
    path = params["path"]
    try:
        return tune_binary(
            load_binary(path),
            params["recipe"],
            Path(path).name,
            params.get("recipe_name") or "recipe.remap",
            skip_validation=bool(params.get("skip_validation", False)),
            force=bool(params.get("force", False)),
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
