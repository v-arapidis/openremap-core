"""
Shared recipe pre-flight checks for the patch/validate service layer.

The same file-size and match-key checks run at every phase of the patch
pipeline (strict validation, existence diagnosis, patched verification) and
also feed ``ECUPatcher.preflight_warnings()``.  They live here once so the
three validators cannot drift apart.

All functions are pure: they accept a recipe dict and binary bytes, return
``None`` when the check passes (or cannot be evaluated), and return a
human-readable warning/error string otherwise.  The caller decides whether
the result is fatal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from openremap.core.services.identify.identifier import identify_ecu


def check_file_size(recipe: dict, actual_size: int) -> Optional[str]:
    """
    Compare a binary's size against the recipe's declared ``file_size``.

    Args:
        recipe:      The recipe dict (``ecu.file_size`` is read from it).
        actual_size: Length in bytes of the binary being checked.

    Returns:
        An error string if sizes mismatch, None if they match (or if the
        recipe carries no file_size field — treated as unverifiable, not
        fatal; the caller decides how to handle it).
    """
    expected_size = recipe.get("ecu", {}).get("file_size")
    if expected_size is None:
        return None  # no size declared — skip

    if actual_size != expected_size:
        return (
            f"File size mismatch: expected {expected_size:,} bytes, "
            f"found {actual_size:,} bytes — possibly a different ECU model."
        )
    return None


def check_same_file_only(
    recipe: dict, target_data: bytes, force: bool = False,
) -> tuple[bool, str]:
    """Policy gate for same-file-only recipes (ISSUE-2 middle ground).

    A recipe stamped ``metadata.portability == "same_file_only"`` (produced
    by ``cook --allow-non-unique`` when non-unique anchors are present) may
    only be applied to the exact binary it was cooked from: the target's
    SHA-256 must equal the recipe's ``ecu.sha256``.  ``force`` overrides the
    refusal — the mechanical phases (strict validation, patcher, post-patch
    verification) still run and can still abort.

    Returns ``(allowed, message)`` — a non-empty message is informational
    (warning) when allowed, the refusal text when not.
    """
    meta = recipe.get("metadata", {})
    if meta.get("portability") != "same_file_only":
        return True, ""
    expected = recipe.get("ecu", {}).get("sha256")
    if not expected:
        return True, (
            "recipe is stamped same-file-only but carries no source sha256 "
            "— cannot enforce the guard"
        )
    actual = hashlib.sha256(target_data).hexdigest()
    if actual == expected:
        return True, ""
    if force:
        return True, (
            "--force: applying a SAME-FILE-ONLY recipe to a binary whose sha256 "
            "differs from the source — non-unique anchors may patch the wrong "
            "location"
        )
    return False, (
        "recipe is stamped SAME-FILE-ONLY (non-unique anchors) and the target's "
        f"sha256 ({actual}) does not match the source ({expected}). It may only "
        "be applied to the exact binary it was cooked from — pass --force to "
        "override."
    )


def check_match_key(recipe: dict, data: bytes, filename: str) -> Optional[str]:
    """
    Identify a binary and compare its match_key against the recipe's.

    Args:
        recipe:   The recipe dict (``ecu.match_key`` is read from it).
        data:     The binary content to identify.
        filename: Name of the binary (used by the identifier).

    Returns:
        A warning string when the match keys differ, None when they match
        (or when either key is absent / identification fails — treated as
        unverifiable, not fatal).
    """
    recipe_key = recipe.get("ecu", {}).get("match_key")
    if not recipe_key:
        return None

    try:
        target_id = identify_ecu(data=data, filename=filename)
    except Exception:
        return None  # identification failed — do not block validation

    target_key = target_id.get("match_key")
    if not target_key:
        return None  # target unrecognised — cannot compare

    if target_key != recipe_key:
        return (
            f"Match key mismatch: recipe is for '{recipe_key}', "
            f"but this binary identifies as '{target_key}'. "
            "This is a different ECU or calibration — patching may corrupt the ECU."
        )
    return None


# ---------------------------------------------------------------------------
# Exact-offset scan — shared by validate_strict (ob) and validate_patched (mb)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExactScanOutcome:
    """One instruction's exact-offset comparison outcome.

    ``kind``:
        "ok"        — bytes at offset match the expected value
        "malformed" — the field is not valid hex (size 0)
        "bounds"    — offset + size exceeds the file length
        "stale"     — bytes match ``compare_field`` instead (original bytes
                      still present — only when ``compare_field`` is given)
        "mismatch"  — bytes match neither
    """

    instruction_index: int
    offset: int
    offset_hex: str
    size: int
    expected: str
    found: str
    passed: bool
    kind: str


def scan_exact_matches(
    data: bytes,
    instructions: Sequence[Dict[str, Any]],
    field: str,
    *,
    compare_field: Optional[str] = None,
) -> List[ExactScanOutcome]:
    """
    Compare every instruction's ``field`` value against the exact bytes at
    its offset.  Shared by the strict validator (``ob``) and the post-patch
    verifier (``mb``) so their read/compare logic cannot drift apart.

    Args:
        data:          Binary content being checked.
        instructions:  Recipe instruction list.
        field:         Instruction key holding the expected hex value.
        compare_field: Optional second instruction key (e.g. ``"ob"`` when
                       checking ``"mb"``): when the found bytes match it, the
                       outcome is ``"stale"`` instead of ``"mismatch"``.
    """
    outcomes: List[ExactScanOutcome] = []
    file_len = len(data)

    for idx, inst in enumerate(instructions, 1):
        offset: int = inst["offset"]
        expected: str = str(inst[field]).upper()
        offset_hex: str = f"{offset:X}"

        try:
            size: int = len(bytes.fromhex(expected))
        except ValueError:
            outcomes.append(
                ExactScanOutcome(idx, offset, offset_hex, 0, expected, "", False, "malformed")
            )
            continue

        if offset < 0 or offset + size > file_len:
            outcomes.append(
                ExactScanOutcome(idx, offset, offset_hex, size, expected, "", False, "bounds")
            )
            continue

        found = data[offset : offset + size].hex().upper()

        if found == expected:
            outcomes.append(
                ExactScanOutcome(idx, offset, offset_hex, size, expected, found, True, "ok")
            )
        elif compare_field and found == str(inst[compare_field]).upper():
            outcomes.append(
                ExactScanOutcome(idx, offset, offset_hex, size, expected, found, False, "stale")
            )
        else:
            outcomes.append(
                ExactScanOutcome(idx, offset, offset_hex, size, expected, found, False, "mismatch")
            )

    return outcomes
