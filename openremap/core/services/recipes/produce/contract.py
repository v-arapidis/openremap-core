"""
Write the schema-5.0 ``contract`` block — and verify it the way apply will.

The contract is small on purpose. Every field has a consumer on the apply side:

``identity_floor``  the floor an apply command must respect (``any`` when the
                    anchors are unique, ``firmware`` when they are not)
``anchors_unique``  re-derived by apply from ``instructions[].ctx_unique``; a
                    mismatch means the recipe was doctored
``sections``        the optional top-level sections that are actually present;
                    anything else is refused by apply

Nothing about checksums is written. A field nobody reads can only go stale or
lie, and a wrong stored checksum region would silently drop real tune bytes —
the one failure direction that is neither visible nor recoverable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from openremap.core.services.recipes.apply.contract import (
    CONTRACT_FIELDS,
    OPTIONAL_SECTIONS,
    ApplyRefused,
    read_contract,
)

__all__ = ["BODY_VERSION", "build_contract", "verify_recipe"]

#: The recipe body version this producer writes.
BODY_VERSION = "5.0"


def _sections(recipe: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(s for s in OPTIONAL_SECTIONS if s in recipe))


def build_contract(
    instructions: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]
) -> dict:
    """Return the contract block for *instructions* in *recipe*.

    ``identity_floor`` follows from anchor uniqueness and nothing else: a unique
    anchor proves its own position, so the recipe needs no identity at all.
    """
    anchors_unique = all(bool(inst.get("ctx_unique")) for inst in instructions)
    return {
        "identity_floor": "any" if anchors_unique else "firmware",
        "anchors_unique": anchors_unique,
        "sections": list(_sections(recipe)),
    }


def verify_recipe(recipe: Mapping[str, Any]) -> None:
    """Refuse to hand back a recipe the apply side would not accept.

    A producer that emits something its consumer rejects is a bug. Running the
    reader here turns that bug into a loud failure at cook time instead of a
    confusing one at apply time.
    """
    missing = [f for f in CONTRACT_FIELDS if f not in (recipe.get("contract") or {})]
    if missing:  # pragma: no cover - build_contract always provides them
        raise RuntimeError(f"cook produced an incomplete contract: missing {missing}")
    try:
        read_contract(recipe)
    except ApplyRefused as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"cook produced a recipe the apply engine refuses ({exc.check}): {exc}"
        ) from exc
