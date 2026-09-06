"""
openremap.core.services.recipes.tune — the 3-phase safe apply.

``validate_before`` → ``patch`` → ``validate_after``, plus the same-file-only
policy gate, with fast-fail on each phase.  Pure bytes/dict in-out; the
``tune`` RPC handler (path I/O + ``GuardError`` translation) stays in the API
gate.  The same-file-only rejection surfaces as ``ValueError`` here and is
translated to ``GuardError`` at the gate.
"""

from __future__ import annotations

import base64

from openremap.core.serialize import to_json_safe
from openremap.core.services.recipes.patcher import ECUPatcher
from openremap.core.services.recipes.preflight import check_same_file_only
from openremap.core.services.recipes.validate_patched import ECUPatchedValidator
from openremap.core.services.recipes.validate_strict import ECUStrictValidator


def _combined_report(
    target_name: str,
    recipe_name: str,
    output_name: str | None,
    p1_report: dict,
    p2_report: dict,
    p3_report: dict,
    p1_skipped: bool,
    p3_skipped: bool,
) -> dict:
    return {
        "target_file": target_name,
        "recipe_file": recipe_name,
        "output_file": str(output_name) if output_name else "",
        "phase_1_validate_before": {"skipped": p1_skipped, **p1_report},
        "phase_2_apply": p2_report,
        "phase_3_validate_after": {"skipped": p3_skipped, **p3_report},
        "success": (
            (p1_skipped or p1_report.get("summary", {}).get("safe_to_patch", False))
            and p2_report.get("summary", {}).get("patch_applied", False)
            and (
                p3_skipped
                or p3_report.get("summary", {}).get("patch_confirmed", False)
            )
        ),
    }


def tune_binary(
    target_data: bytes,
    recipe: dict,
    target_name: str,
    recipe_name: str,
    *,
    skip_validation: bool = False,
    force: bool = False,
    output_name: str | None = None,
) -> dict:
    """Run the 3-phase safe tune. Returns the combined report + tuned bytes."""
    # ── Policy gate: same-file-only recipes ──────────────────────────
    allowed, gate_msg = check_same_file_only(recipe, target_data, force)
    if not allowed:
        raise ValueError(gate_msg)

    p1_ok = True
    p1_report: dict = {}
    p1_size_warn = None
    p1_match_key_warn = None
    target_md5 = "—"
    if not skip_validation:
        validator = ECUStrictValidator(
            target_data=target_data,
            recipe=recipe,
            target_name=target_name,
            recipe_name=recipe_name,
        )
        p1_size_warn = validator.check_file_size()
        p1_match_key_warn = validator.check_match_key()
        validator.validate_all()
        p1_report = validator.to_dict()
        p1_ok = p1_report.get("summary", {}).get("safe_to_patch", False)
        target_md5 = p1_report.get("target_md5", "?")
        if not p1_ok:
            combined = _combined_report(
                target_name, recipe_name, output_name,
                p1_report, {}, {}, False, skip_validation,
            )
            return {
                "combined": to_json_safe(combined),
                "all_ok": False,
                "tuned_b64": None,
                "target_md5": target_md5,
                "tuned_md5": "?",
                "gate_msg": gate_msg,
                "p1_size_warn": p1_size_warn,
                "p1_match_key_warn": p1_match_key_warn,
                "apply_error": None,
            }

    # ── Phase 2: apply (skip_validation=True — Phase 1 already ran) ──
    apply_error = None
    tuned_bytes = b""
    try:
        patcher = ECUPatcher(
            target_data=target_data,
            recipe=recipe,
            target_name=target_name,
            recipe_name=recipe_name,
            skip_validation=True,
        )
        tuned_bytes = patcher.apply_all()
    except ValueError as exc:
        apply_error = f"Apply rejected — {exc}"
    except Exception as exc:
        apply_error = f"Apply failed unexpectedly — {exc}"

    if apply_error:
        combined = _combined_report(
            target_name, recipe_name, output_name,
            p1_report, {}, {}, skip_validation, skip_validation,
        )
        return {
            "combined": to_json_safe(combined),
            "all_ok": False,
            "tuned_b64": None,
            "target_md5": target_md5,
            "tuned_md5": "?",
            "gate_msg": gate_msg,
            "p1_size_warn": p1_size_warn,
            "p1_match_key_warn": p1_match_key_warn,
            "apply_error": apply_error,
        }

    p2_report = patcher.to_dict(patched_data=tuned_bytes)
    p2_ok = p2_report.get("summary", {}).get("patch_applied", False)
    tuned_md5 = p2_report.get("summary", {}).get("patched_md5", "?")
    if not p2_ok:
        combined = _combined_report(
            target_name, recipe_name, output_name,
            p1_report, p2_report, {}, skip_validation, skip_validation,
        )
        return {
            "combined": to_json_safe(combined),
            "all_ok": False,
            "tuned_b64": None,
            "target_md5": target_md5,
            "tuned_md5": tuned_md5,
            "gate_msg": gate_msg,
            "p1_size_warn": p1_size_warn,
            "p1_match_key_warn": p1_match_key_warn,
            "apply_error": None,
        }

    # ── Phase 3: validate after ──────────────────────────────────────
    p3_ok = True
    p3_report: dict = {}
    if not skip_validation:
        validator = ECUPatchedValidator(
            patched_data=tuned_bytes,
            recipe=recipe,
            patched_name=output_name or target_name,
            recipe_name=recipe_name,
        )
        validator.verify_all()
        p3_report = validator.to_dict()
        p3_ok = p3_report.get("summary", {}).get("patch_confirmed", False)

    all_ok = p1_ok and p2_ok and p3_ok
    combined = _combined_report(
        target_name, recipe_name, output_name,
        p1_report, p2_report, p3_report, skip_validation, skip_validation,
    )

    return {
        "combined": to_json_safe(combined),
        "all_ok": all_ok,
        "tuned_b64": base64.b64encode(tuned_bytes).decode() if p2_ok else None,
        "target_md5": target_md5,
        "tuned_md5": tuned_md5,
        "gate_msg": gate_msg,
        "p1_size_warn": p1_size_warn,
        "p1_match_key_warn": p1_match_key_warn,
        "apply_error": None,
    }
