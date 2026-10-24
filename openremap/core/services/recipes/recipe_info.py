"""
Read a recipe and say what it is — without applying it.

The apply engine can only tell you about a recipe by *running* it: the A-rules
fire during the pipeline, against a target. But the questions a person has
before applying are about the recipe itself — is this 5.0, how many
instructions, what does it need to be applied to, which file was it cooked
from? This service answers those by running the same 5.0 reader the engine
uses, so a recipe that fails here would fail there too, for the same stated
reason.

Nothing here touches a target binary: a recipe is a document, and this module
only reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openremap.core.services.recipes.apply.contract import read_contract

__all__ = ["recipe_info"]


def _statistics(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """The producer's own summary, filtered to what a reader can act on."""
    raw = recipe.get("statistics")
    if not isinstance(raw, Mapping):
        return {}
    keep = (
        "total_bytes_changed",
        "percentage_changed",
        "single_byte_changes",
        "multi_byte_changes",
        "largest_change_size",
    )
    return {key: raw[key] for key in keep if key in raw}


def _instruction_summary(recipe: Mapping[str, Any], limit: int) -> tuple[list[dict], bool]:
    """One compact row per instruction — the detail view's list, capped.

    Capped because a recipe can carry thousands and the caller asked for a
    *view*: ``truncated`` says so instead of quietly dropping the rest.
    """
    instructions = recipe.get("instructions") or []
    out: list[dict] = []
    for index, instruction in enumerate(instructions[:limit], 1):
        if not isinstance(instruction, Mapping):
            continue
        flags = [
            str(flag.get("kind"))
            for flag in instruction.get("flags") or []
            if isinstance(flag, Mapping) and flag.get("kind")
        ]
        out.append(
            {
                "index": index,
                "offset": int(instruction.get("offset") or 0),
                "size": int(instruction.get("size") or 0),
                "region": instruction.get("region"),
                "flags": flags,
                "ctx_unique": bool(instruction.get("ctx_unique")),
            }
        )
    return out, len(instructions) > limit


def recipe_info(
    recipe: Mapping[str, Any],
    *,
    name: str = "",
    include_instructions: bool = False,
    instruction_limit: int = 2000,
) -> dict[str, Any]:
    """Return what a 5.0 recipe declares about itself.

    Raises:
        ApplyRefused: with the rule id (A1–A5, A12) that makes the document
            unusable — the same ids the apply engine reports.
    """
    contract = read_contract(recipe)
    instructions = recipe.get("instructions") or []
    source = recipe.get("source") if isinstance(recipe.get("source"), Mapping) else {}
    tune = recipe.get("tune") if isinstance(recipe.get("tune"), Mapping) else {}
    producer = recipe.get("producer") if isinstance(recipe.get("producer"), Mapping) else {}
    metadata = recipe.get("metadata") if isinstance(recipe.get("metadata"), Mapping) else {}

    result = {
        "name": name or str(metadata.get("name") or ""),
        "contract": contract.as_dict(),
        "schema": contract.schema,
        "instructions": len(instructions),
        "bytes_written": sum(len(str(i.get("mb") or "")) // 2 for i in instructions),
        "source": {
            "filename": source.get("filename"),
            "size": source.get("size"),
            "sha256": source.get("sha256"),
            "match_key": (source.get("identity") or {}).get("match_key")
            if isinstance(source.get("identity"), Mapping)
            else None,
        },
        "tune": {
            "filename": tune.get("filename"),
            "size": tune.get("size"),
            "sha256": tune.get("sha256"),
        },
        "producer": {
            "engine": producer.get("engine"),
            "version": producer.get("version"),
            "command": producer.get("command"),
            "created_at": producer.get("created_at"),
        },
        "statistics": _statistics(recipe),
    }
    if include_instructions:
        rows, truncated = _instruction_summary(recipe, instruction_limit)
        result["instruction_rows"] = rows
        result["instructions_truncated"] = truncated
    return result
