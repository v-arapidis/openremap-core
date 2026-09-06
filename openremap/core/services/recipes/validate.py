"""
openremap.core.services.recipes.validate — validate a binary against a recipe.

Three modes, mirroring the CLI's ``validate before/check/after``:

  validate_before_binary — strict pre-flight (ob bytes at recorded offsets).
  validate_check_binary  — whole-binary existence search (diagnostic).
  validate_after_binary  — post-tune confirmation (mb bytes written).

Pure bytes/dict in-out; the ``validate_*`` RPC handlers (path I/O) stay in the
API gate.
"""

from __future__ import annotations

from openremap.core.serialize import to_json_safe
from openremap.core.services.recipes.preflight import check_same_file_only
from openremap.core.services.recipes.validate_exists import ECUExistenceValidator
from openremap.core.services.recipes.validate_patched import ECUPatchedValidator
from openremap.core.services.recipes.validate_strict import ECUStrictValidator


def validate_before_binary(
    target_data: bytes, recipe: dict, target_name: str, recipe_name: str
) -> dict:
    """Pre-flight strict check + same-file-only gate. Returns the report dict."""
    validator = ECUStrictValidator(
        target_data=target_data,
        recipe=recipe,
        target_name=target_name,
        recipe_name=recipe_name,
    )
    size_warn = validator.check_file_size()
    match_key_warn = validator.check_match_key()
    validator.validate_all()
    report = validator.to_dict()

    gate_ok, gate_msg = check_same_file_only(recipe, target_data)
    stamped = recipe.get("metadata", {}).get("portability") == "same_file_only"
    report["same_file_only"] = {"stamped": stamped, "allowed": gate_ok, "note": gate_msg}
    if stamped and not gate_ok:
        report["summary"]["safe_to_patch"] = False

    return to_json_safe(
        {"report": report, "size_warn": size_warn, "match_key_warn": match_key_warn}
    )


def validate_check_binary(
    target_data: bytes, recipe: dict, target_name: str, recipe_name: str
) -> dict:
    """Whole-binary existence search (diagnostic). Returns the report dict."""
    validator = ECUExistenceValidator(
        target_data=target_data,
        recipe=recipe,
        target_name=target_name,
        recipe_name=recipe_name,
    )
    size_warn = validator.check_file_size()
    match_key_warn = validator.check_match_key()
    validator.validate_all()
    report = validator.to_dict()

    return to_json_safe(
        {"report": report, "size_warn": size_warn, "match_key_warn": match_key_warn}
    )


def validate_after_binary(
    patched_data: bytes, recipe: dict, patched_name: str, recipe_name: str
) -> dict:
    """Post-tune confirmation (mb bytes written). Returns the report dict."""
    validator = ECUPatchedValidator(
        patched_data=patched_data,
        recipe=recipe,
        patched_name=patched_name,
        recipe_name=recipe_name,
    )
    size_warn = validator.check_file_size()
    match_key_warn = validator.check_match_key()
    validator.verify_all()
    report = validator.to_dict()

    return to_json_safe(
        {"report": report, "size_warn": size_warn, "match_key_warn": match_key_warn}
    )
