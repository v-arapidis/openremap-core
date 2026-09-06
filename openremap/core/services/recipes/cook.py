"""
openremap.core.services.recipes.cook — diff two binaries into a .remap recipe.

The cook pipeline core: ``ECUDiffAnalyzer`` (diff) → ``scan_map_tables`` (map
scan) → ``attach_maps`` (annotation) → ``tag_instruction_regions`` (region
tags).  Pure bytes-in / dict-out; the ``cook`` RPC handler (path I/O +
``GuardError`` translation) stays in the API gate.

Expected business-rule failures (size mismatch, non-unique anchors) surface as
``ValueError`` from the analyzer — the gate translates those to ``GuardError``.
"""

from __future__ import annotations

from openremap.core.serialize import to_json_safe
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.recipe_maps import attach_maps
from openremap.core.services.recipes.recipe_regions import tag_instruction_regions


def _tag_regions(recipe: dict, original_data: bytes, tables=None) -> dict:
    """Advisory flash-layout region tags — never blocks, never filters."""
    try:
        return tag_instruction_regions(recipe, original_data, tables)
    except Exception:
        return {"tagged": 0, "risky": 0, "by_region": {}}


def cook_recipe(
    original_data: bytes,
    modified_data: bytes,
    original_name: str,
    modified_name: str,
    *,
    context_size: int = 32,
    require_unique: bool = True,
    annotate_maps: bool = True,
    description: str | None = None,
) -> dict:
    """Diff two binaries into a recipe, returning the CLI-shaped result dict."""
    analyzer = ECUDiffAnalyzer(
        original_data=original_data,
        modified_data=modified_data,
        original_filename=original_name,
        modified_filename=modified_name,
        context_size=context_size,
        require_unique=require_unique,
    )
    recipe = analyzer.build_recipe(description=description)

    # One structural scan, shared by map annotation and region tags.
    shared_tables = scan_map_tables(original_data, min_score=0.55, max_series_tables=16)

    map_count = None
    if annotate_maps:
        attach_maps(recipe, original_data, tables=shared_tables)
        map_count = len(recipe["maps"])

    region_summary = _tag_regions(recipe, original_data, shared_tables)

    warnings = analyzer.cook_warnings()

    if not require_unique:
        non_unique_warnings = [w for w in warnings if "non-unique" in w]
        if non_unique_warnings:
            recipe.setdefault("metadata", {})["portability"] = "same_file_only"

    return to_json_safe(
        {
            "recipe": recipe,
            "warnings": warnings,
            "map_count": map_count,
            "region_summary": region_summary,
        }
    )
