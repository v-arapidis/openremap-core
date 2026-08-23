"""
Recipe merge — combine two recipes into one, validated against a common
stock binary.

Model (like git's three-way merge): the stock binary is the common
ancestor, recipe A is "ours", recipe B is "theirs".  Neither tuner needs
the other's exact original file — every instruction from both recipes is
validated against the shared stock (ob-at-offset, strict-validator
semantics), so a recipe built from a slightly different original (VIN
area, metadata) fails only for the instructions that truly differ.

This is safe because the patcher searches anchors in a frozen snapshot of
the original binary (patcher.py) — a merged instruction list applies
exactly like a single recipe.

Merge rules
-----------
- Same offset, same size + ob + mb  → one copy kept.
- Same offset, anything else        → CONFLICT (same address, different
  edit — a human must decide).
- Overlapping ranges, different offsets → CONFLICT (edit boundaries
  disagree — a human must decide).
- Different, non-overlapping offsets → both kept.

Without ``stock_data`` the merge falls back to strict mode: both recipes
must declare identical ``ecu.sha256`` + ``match_key``.
"""

from __future__ import annotations

from openremap.core.services.entropy import count_unique_in_window
from openremap.core.services.recipes.preflight import check_file_size
from openremap.core.services.recipes.recipe_builder import (
    build_creator_block,
    check_schema_version,
    compute_fingerprint,
)


class MergeConflict(Exception):
    """The two recipes cannot be combined automatically."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(inst: dict) -> tuple[int, int]:
    return inst["offset"], inst["offset"] + inst["size"]


def _compute_stats(instructions: list[dict], file_size: int | None) -> dict:
    """Recompute the statistics block for a merged instruction list."""
    if not instructions:
        return {}
    total_changed = sum(i["size"] for i in instructions)
    single = sum(1 for i in instructions if i["size"] == 1)
    ctx_sizes = [len(i.get("ctx", "")) // 2 for i in instructions]
    stats: dict = {
        "total_changes": len(instructions),
        "total_bytes_changed": total_changed,
        "single_byte_changes": single,
        "multi_byte_changes": len(instructions) - single,
        "largest_change_size": max(i["size"] for i in instructions),
        "smallest_change_size": min(i["size"] for i in instructions),
        "min_context_size": min(ctx_sizes) if ctx_sizes else 0,
        "max_context_size": max(ctx_sizes) if ctx_sizes else 0,
    }
    if file_size:
        stats["percentage_changed"] = round(total_changed / file_size * 100, 4)
    return stats


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_recipes(
    recipe_a: dict,
    recipe_b: dict,
    *,
    name_a: str = "a.remap",
    name_b: str = "b.remap",
    stock_data: bytes | None = None,
    strict: bool = False,
) -> dict:
    """
    Merge two recipes into one.

    Args:
        recipe_a / recipe_b: Recipe dicts (schema >= 4.3).
        name_a / name_b:     Source names used in messages and metadata.
        stock_data:          The common stock binary.  When given, every
                             instruction of both recipes is validated
                             against it; instructions that fail are
                             excluded (reported as warnings) — or the
                             merge aborts when ``strict`` is True.
        strict:              Abort instead of excluding mismatched
                             instructions.

    Returns:
        The merged recipe (schema 4.4 when ``stock_data`` allows map
        re-annotation, else 4.3).

    Raises:
        MergeConflict: on ECU mismatch, on same-offset / overlapping-range
                       conflicts, and on strict-mode failures.
    """
    for name, recipe in ((name_a, recipe_a), (name_b, recipe_b)):
        try:
            check_schema_version(recipe)
        except ValueError as exc:
            raise MergeConflict(f"{name}: {exc}") from exc

    ecu_a = recipe_a.get("ecu", {})
    ecu_b = recipe_b.get("ecu", {})
    warnings: list[str] = []

    # ── volatile-section handling ──────────────────────────────────────
    # A merged recipe combines two patch lists into one; the per-recipe
    # volatile exclusion evidence (schema 4.5) does not transfer — the
    # merged instruction set is a different list, and re-deriving exclusions
    # is cook-volatile's job, not merge's.  Drop it, and say so.
    volatile_sources = [
        name
        for name, r in ((name_a, recipe_a), (name_b, recipe_b))
        if isinstance(r.get("volatile"), dict)
    ]
    if volatile_sources:
        warnings.append(
            "volatile section dropped — the merged recipe combines two "
            "patch lists and re-derives exclusions; the excluded-volatile "
            "evidence from " + ", ".join(volatile_sources) + " does not "
            "transfer. Re-run cook-volatile on the merged pair for a "
            "portable recipe."
        )

    # ── ECU gate ──────────────────────────────────────────────────────
    key_a = ecu_a.get("match_key")
    key_b = ecu_b.get("match_key")
    if key_a and key_b and key_a != key_b:
        raise MergeConflict(
            f"ECU mismatch: '{name_a}' is for '{key_a}', '{name_b}' is "
            f"for '{key_b}'. Refusing to merge recipes from different ECUs."
        )

    if stock_data is None:
        sha_a = ecu_a.get("sha256")
        sha_b = ecu_b.get("sha256")
        if not (sha_a and sha_b and sha_a == sha_b):
            raise MergeConflict(
                "Cannot merge without --stock: the recipes were not built "
                "from byte-identical originals (ecu.sha256 differs or is "
                "missing). Pass --stock <original.bin> to merge against "
                "a common original."
            )
    else:
        for name, recipe in ((name_a, recipe_a), (name_b, recipe_b)):
            size_error = check_file_size(recipe, len(stock_data))
            if size_error:
                warnings.append(f"{name}: {size_error}")

    # ── Validate + collect instructions ───────────────────────────────
    def validated(recipe: dict, name: str) -> list[dict]:
        kept: list[dict] = []
        excluded: list[str] = []
        for idx, inst in enumerate(recipe.get("instructions", []), 1):
            off = inst["offset"]
            size = inst.get("size") or len(bytes.fromhex(inst["ob"]))
            ob = str(inst["ob"]).upper()
            if stock_data is not None:
                if off < 0 or off + size > len(stock_data):
                    excluded.append(f"#{idx} at 0x{off:X} out of file range")
                    continue
                actual = stock_data[off : off + size].hex().upper()
                if actual != ob:
                    excluded.append(
                        f"#{idx} at 0x{off:X}: stock has {actual}, "
                        f"recipe expects {ob}"
                    )
                    continue
            kept.append(inst)
        if excluded:
            if strict:
                raise MergeConflict(
                    f"{name}: {len(excluded)} instruction(s) do not match "
                    f"the stock binary (" + "; ".join(excluded[:3])
                    + ("; …" if len(excluded) > 3 else "")
                    + "). Aborting in --strict mode."
                )
            warnings.append(
                f"{name}: {len(excluded)} instruction(s) skipped — they do "
                f"not match the stock binary (" + "; ".join(excluded[:3])
                + ("; …" if len(excluded) > 3 else "")
                + "). This recipe was likely built from a slightly "
                "different original."
            )
        return kept

    insts_a = validated(recipe_a, name_a)
    insts_b = validated(recipe_b, name_b)

    # ── Combine with conflict detection ───────────────────────────────
    merged: dict[int, dict] = {}

    def add(inst: dict, source: str) -> None:
        key = inst["offset"]
        if key in merged:
            existing = merged[key]
            if (
                existing["size"] == inst["size"]
                and existing["ob"] == inst["ob"]
                and existing["mb"] == inst["mb"]
            ):
                return  # identical edit in both recipes — keep one copy
            raise MergeConflict(
                f"Conflict at 0x{key:X}: '{source}' wants "
                f"{inst['ob']}→{inst['mb']} ({inst['size']}B), but the "
                f"other recipe sets {existing['ob']}→{existing['mb']} "
                f"({existing['size']}B). Same address, different edit — "
                f"merge by hand."
            )
        for other in merged.values():
            os_, oe = _span(inst)
            ks, ke = _span(other)
            if os_ < ke and ks < oe:
                raise MergeConflict(
                    f"Overlapping edits at 0x{os_:X} and 0x{ks:X} — the "
                    f"recipes changed the same region with different "
                    f"boundaries. Merge by hand."
                )
        merged[key] = inst

    for inst in insts_a:
        add(inst, name_a)
    for inst in insts_b:
        add(inst, name_b)

    instructions = sorted(merged.values(), key=lambda i: i["offset"])

    # ── Guard 3 re-check on the merged set ────────────────────────────
    if stock_data is not None:
        non_unique: list[str] = []
        for inst in instructions:
            anchor = bytes.fromhex(inst.get("ctx", "")) + bytes.fromhex(inst["ob"])
            if not anchor:
                continue
            count = count_unique_in_window(stock_data, anchor, 0, len(stock_data))
            if count != 1:
                non_unique.append(f"0x{inst['offset']:X} (ctx+ob matches {count}×)")
        if non_unique:
            msg = (
                f"{len(non_unique)} merged instruction(s) have non-unique "
                f"anchors in the stock binary: "
                + "; ".join(non_unique[:3])
                + ("; …" if len(non_unique) > 3 else "")
                + ". Applying the merged recipe to a DIFFERENT revision "
                "may patch the wrong location."
            )
            if strict:
                raise MergeConflict(msg)
            warnings.append(msg)

    # ── Build the merged recipe ───────────────────────────────────────
    meta_a = recipe_a.get("metadata", {})
    file_size = ecu_a.get("file_size")
    merged_recipe: dict = {
        "type": "recipe",
        "schema_version": "4.3",
        "source": "recipe_merge",
        "application": "openremap-core",
        "creator": build_creator_block(),
        "fingerprint": compute_fingerprint(instructions),
        "metadata": {
            "name": f"merge: {name_a} + {name_b}",
            "description": (
                f"Merged from '{name_a}' and '{name_b}' — see merged_from."
            ),
            "tags": [],
            "instruction_count": len(instructions),
            "original_file": meta_a.get("original_file"),
            "modified_file": None,
            "original_size": file_size,
            "modified_size": file_size,
            "tune_id": None,
            "merged_from": [name_a, name_b],
            "merged_fingerprints": [
                recipe_a.get("fingerprint"),
                recipe_b.get("fingerprint"),
            ],
        },
        "ecu": {**ecu_a, "cook_warnings": warnings},
        "statistics": _compute_stats(instructions, file_size),
        "instructions": instructions,
    }

    # Re-annotate maps from the stock (fresh refs — merged recipes may
    # combine instructions from differently-annotated sources).
    if stock_data is not None:
        try:
            from openremap.core.services.recipes.recipe_maps import attach_maps

            attach_maps(merged_recipe, stock_data)
        except Exception:
            pass  # best-effort — annotation never blocks a merge

    return merged_recipe
