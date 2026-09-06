"""
openremap.core.services.recipes.cook_volatile — cook a car-portable recipe.

The ``cook`` pipeline (diff → map scan → annotation → region tags) plus a
volatile-classification pass (``classify_volatile``) and the exclusion filter.
Pure bytes/dict in-out; the ``cook_volatile`` RPC handler (path I/O +
``GuardError`` translation) stays in the API gate.

Expected business-rule failures surface as ``ValueError`` from the analyzer —
the gate translates those to ``GuardError``.
"""

from __future__ import annotations

from dataclasses import replace

from openremap.core.serialize import to_json_safe
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.cook import _tag_regions
from openremap.core.services.recipes.recipe_builder import (
    ECUDiffAnalyzer,
    compute_fingerprint,
)
from openremap.core.services.recipes.recipe_maps import attach_maps
from openremap.core.services.recipes.volatile import (
    VolatileFinding,
    classify_volatile,
)


def _recompute_stats(
    instructions: list,
    file_size: int,
    context_size: int,
    max_context_size: int,
) -> dict:
    """Statistical summary of the KEPT instruction set."""
    if not instructions:
        return {}

    total_changed = sum(inst["size"] for inst in instructions)
    single = sum(1 for inst in instructions if inst["size"] == 1)

    return {
        "total_changes": len(instructions),
        "total_bytes_changed": total_changed,
        "percentage_changed": round(total_changed / file_size * 100, 4),
        "single_byte_changes": single,
        "multi_byte_changes": len(instructions) - single,
        "largest_change_size": max(inst["size"] for inst in instructions),
        "smallest_change_size": min(inst["size"] for inst in instructions),
        "min_context_size": context_size,
        "max_context_size": max_context_size,
    }


def cook_volatile_recipe(
    original_data: bytes,
    modified_data: bytes,
    original_name: str,
    modified_name: str,
    *,
    context_size: int = 32,
    require_unique: bool = True,
    no_exclude: bool = False,
    exclude_uncertain: bool = False,
    annotate_maps: bool = True,
) -> dict:
    """Cook a car-portable recipe (volatile instructions excluded)."""
    analyzer = ECUDiffAnalyzer(
        original_data=original_data,
        modified_data=modified_data,
        original_filename=original_name,
        modified_filename=modified_name,
        context_size=context_size,
        require_unique=require_unique,
    )
    recipe = analyzer.build_recipe()

    report = classify_volatile(
        recipe,
        original_data,
        exclude_uncertain=exclude_uncertain,
    )

    # --- Filter: drop near-certain volatile instructions ---------------
    if not no_exclude:
        excluded_indices = {f.index for f in report.excluded}
        recipe["instructions"] = [
            inst
            for i, inst in enumerate(recipe["instructions"])
            if i not in excluded_indices
        ]

    # Recompute everything derived from the instruction set.
    recipe["statistics"] = _recompute_stats(
        recipe["instructions"],
        len(original_data),
        analyzer.context_size,
        analyzer.max_context_size,
    )
    recipe["fingerprint"] = compute_fingerprint(recipe["instructions"])
    recipe["metadata"]["instruction_count"] = len(recipe["instructions"])

    # --- volatile section (schema 4.5) ---------------------------------
    if no_exclude:
        excluded_findings: list[VolatileFinding] = []
        flagged_findings: list[VolatileFinding] = [
            replace(f, action="flagged")
            for f in (*report.excluded, *report.flagged)
        ]
    else:
        excluded_findings = list(report.excluded)
        flagged_findings = list(report.flagged)

    volatile_summary = {
        "excluded_count": len(excluded_findings),
        "flagged_count": len(flagged_findings),
        "bytes_excluded": sum(f.size for f in excluded_findings),
    }
    recipe["volatile"] = {
        "excluded": [f.to_dict() for f in excluded_findings],
        "flagged": [f.to_dict() for f in flagged_findings],
        "summary": volatile_summary,
    }
    recipe["metadata"]["source"] = "cook_volatile"
    recipe["metadata"]["volatile"] = volatile_summary
    recipe["metadata"]["excluded_volatile"] = not no_exclude

    # One structural scan, shared by map annotation and region tags.
    shared_tables = scan_map_tables(
        original_data, min_score=0.55, max_series_tables=16,
    )

    map_count = None
    if annotate_maps:
        attach_maps(recipe, original_data, tables=shared_tables)
        map_count = len(recipe["maps"])

    # attach_maps bumps schema to 4.4 (MAPS_SCHEMA_VERSION) — set the
    # volatile schema level AFTER it, so 4.5 always wins.
    recipe["schema_version"] = "4.5"

    # Advisory flash-layout region tags on the KEPT instruction set.
    region_summary = _tag_regions(recipe, original_data, shared_tables)

    # Same-file-only stamp.
    warnings = analyzer.cook_warnings()
    if not require_unique and any("non-unique" in w for w in warnings):
        recipe.setdefault("metadata", {})["portability"] = "same_file_only"

    return to_json_safe(
        {
            "recipe": recipe,
            "excluded_findings": [f.to_dict() for f in excluded_findings],
            "flagged_findings": [f.to_dict() for f in flagged_findings],
            "region_summary": region_summary,
            "map_count": map_count,
            "warnings": warnings,
        }
    )
