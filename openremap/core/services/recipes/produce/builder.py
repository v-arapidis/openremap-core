"""
The cook pipeline — builds a schema-5.0 recipe under an explicit cook mode.

Order, each step recording something in the report:

1. **Size guard** — both images must be the same length; otherwise the offsets
   mean nothing.
2. **Identity guard** — both binaries are identified once. If both identify and
   their ``match_key`` differs, the cook refuses: a recipe may only be born from
   one firmware. (When neither identifies, the pair is accepted with an explicit
   note — the operator asserts the pairing, and requiring identification would
   make the producer useless for exactly the dumps ``tune-unique`` exists for.)
3. **Diff + anchors** — the proven core (``ECUDiffAnalyzer``): byte diff with a
   16-byte merge, and per-change anchor expansion 32 -> 512 bytes requiring
   uniqueness and entropy. ``cook-strict`` refuses a non-unique anchor here.
4. **Annotate** — the annotator's advisory flags (VIN, low entropy) plus the
   flash-region tags. Nothing is removed; everything is reported.
5. **Maps** — the structural map layer, attached to the kept instructions.
6. **Assemble 5.0** — the envelope: contract, producer, source, tune,
   fingerprint, statistics. The result is then verified against the apply-side
   reader before it is returned.

Nothing is excluded at any step. That is deliberate and measured: see the
package docstring.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from openremap import __version__
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.produce import report as report_mod
from openremap.core.services.recipes.produce.contract import (
    BODY_VERSION,
    build_contract,
    verify_recipe,
)
from openremap.core.services.recipes.produce.modes import CookMode
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.recipe_maps import attach_maps
from openremap.core.services.recipes.recipe_regions import tag_instruction_regions

__all__ = ["produce_recipe"]

#: Identity fields carried into ``source.identity`` (full block, not the lean
#: subset 4.x kept — apply reports them and costs one identify call anyway).
_IDENTITY_FIELDS = (
    "match_key",
    "manufacturer",
    "ecu_family",
    "ecu_variant",
    "software_version",
    "hardware_number",
    "oem_part_number",
    "calibration_id",
    "platform",
    "confidence",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _identity_block(identified: Mapping[str, Any]) -> dict:
    return {k: identified.get(k) for k in _IDENTITY_FIELDS if identified.get(k) is not None}


def _region_summary(recipe: dict, stock_data: bytes, tables) -> dict:
    """Advisory flash-layout region tags — never blocks, never filters."""
    try:
        return tag_instruction_regions(recipe, stock_data, tables)
    except Exception:  # noqa: BLE001 - advisory only, never blocks a cook
        return {"tagged": 0, "risky": 0, "by_region": {}}


def _refuse(report: dict, rule: str, message: str) -> dict:
    report_mod.refuse(report, rule, message)
    return {"recipe": None, "report": report}


def produce_recipe(
    stock_data: bytes,
    tuned_data: bytes,
    stock_name: str,
    tuned_name: str,
    *,
    mode: CookMode,
    context_size: int = 32,
    description: str | None = None,
) -> dict:
    """Build a 5.0 recipe from *stock_data* -> *tuned_data* under *mode*.

    Returns ``{"recipe": dict | None, "report": dict}``. A refusal is an
    expected outcome (``recipe is None`` plus the rule that stopped it), not an
    exception.
    """
    report = report_mod.new_report(
        command=mode.command,
        target_stock=stock_name,
        target_tuned=tuned_name,
        require_unique=mode.require_unique,
    )

    # ---- 1. Size -------------------------------------------------------
    if len(stock_data) != len(tuned_data):
        return _refuse(
            report,
            "size",
            f"Size: stock={len(stock_data):,} B · tuned={len(tuned_data):,} B. "
            "Two different sizes mean two different images.",
        )
    report["counts"]["size"] = len(stock_data)

    # ---- 2. Identity ---------------------------------------------------
    stock_id = identify_ecu(data=stock_data, filename=stock_name)
    tuned_id = identify_ecu(data=tuned_data, filename=tuned_name)
    stock_key = stock_id.get("match_key")
    tuned_key = tuned_id.get("match_key")
    report["identity"] = {
        "stock_match_key": stock_key,
        "tuned_match_key": tuned_key,
        "verifiable": bool(stock_key and tuned_key),
    }
    if stock_key and tuned_key and stock_key != tuned_key:
        return _refuse(
            report,
            "identity",
            f"Different firmware: stock={stock_key} · tuned={tuned_key}. "
            "A recipe between two firmwares would describe a rewrite, not a tune.",
        )
    identity_verifiable = bool(stock_key and tuned_key)
    if not identity_verifiable:
        report_mod.add_note(
            report,
            "Identity does not verify (one or both files are not recognised). "
            "The pair was accepted as-is — confirming it is your call.",
        )

    # ---- 3. Diff + anchors --------------------------------------------
    analyzer = ECUDiffAnalyzer(
        original_data=stock_data,
        modified_data=tuned_data,
        original_filename=stock_name,
        modified_filename=tuned_name,
        context_size=context_size,
        require_unique=mode.require_unique,
    )
    try:
        base = analyzer.build_recipe(description=description)
    except ValueError as exc:
        message = str(exc)
        rule = "unique" if "non-unique" in message else "empty"
        return _refuse(report, rule, message)

    instructions = base["instructions"]

    # Two identical files diff to nothing. The analyzer returns an empty recipe
    # rather than raising, and an empty recipe fails the apply reader (A12) — so
    # refuse here, while the reason is still known. Picking the same file twice
    # is the easiest mistake a user can make, and it must read as a refusal, not
    # as an engine crash.
    if not instructions:
        return _refuse(
            report,
            "empty",
            "The two files have no differences — there is nothing to record in a recipe.",
        )

    # A recipe whose anchors are not unique can only be applied by a command
    # that verifies identity (its floor is ``firmware``). Without a match_key
    # nothing could ever apply it, so refuse instead of handing over a dead
    # document. Note this is decided AFTER the diff: ``cook-forced`` on a pair
    # whose anchors all happen to be unique still yields a floor-any recipe and
    # is perfectly fine.
    anchors_unique = all(bool(i.get("ctx_unique")) for i in instructions)
    if not anchors_unique and not identity_verifiable:
        return _refuse(
            report,
            "identity_required",
            "The pair is not recognised and the anchors are not unique: such a "
            "recipe can only be applied where the identity verifies, so it could "
            "not be applied anywhere.",
        )

    warnings = analyzer.cook_warnings()
    report["warnings"] = list(warnings)
    report["counts"].update(
        {
            "instructions": len(instructions),
            "bytes_changed": sum(int(i.get("size") or 0) for i in instructions),
            "anchors_unique": anchors_unique,
        }
    )

    # ---- 4+5. Regions and maps ----------------------------------------
    tables = scan_map_tables(stock_data, min_score=0.55, max_series_tables=16)

    recipe: dict[str, Any] = {
        "type": "recipe",
        "schema_version": BODY_VERSION,
        "producer": {
            "engine": "openremap",
            "version": __version__,
            "command": mode.command,
            "created_at": _now(),
            "warnings": list(warnings),
        },
        "source": {
            "filename": stock_name,
            "size": len(stock_data),
            "sha256": _sha256(stock_data),
            "identity": _identity_block(stock_id),
        },
        "tune": {
            "filename": tuned_name,
            "size": len(tuned_data),
            "sha256": _sha256(tuned_data),
        },
        "fingerprint": base.get("fingerprint"),
        "metadata": {
            "name": tuned_name,
            "description": description or "",
            "tags": [],
            "tune_id": None,
        },
        "statistics": base.get("statistics") or {},
        "instructions": instructions,
    }

    attach_maps(recipe, stock_data, tables=tables)  # sets schema 4.4 - overridden below
    # attach_maps stamps a 4.x metadata key; 5.0 metadata stays lean and the
    # contract's ``sections`` already declares that maps are present.
    recipe["metadata"].pop("annotated_maps", None)
    region_summary = _region_summary(recipe, stock_data, tables)
    recipe["regions"] = dict(region_summary)
    recipe["schema_version"] = BODY_VERSION

    report["regions"] = dict(region_summary)
    report["flags"] = report_mod.flags_by_kind(instructions)
    report["counts"]["maps"] = len(recipe.get("maps") or [])

    # ---- 6. Contract + self-check --------------------------------------
    recipe["contract"] = build_contract(instructions, recipe)
    verify_recipe(recipe)
    report["contract"] = dict(recipe["contract"])

    report["result"] = {
        "produced": True,
        "schema": BODY_VERSION,
        "instructions": len(instructions),
        "bytes_changed": report["counts"]["bytes_changed"],
        "fingerprint": recipe.get("fingerprint"),
    }
    return {"recipe": recipe, "report": report}
