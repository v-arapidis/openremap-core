"""
ECU Strict Offset Validator
============================
Validates a target ECU binary against a recipe by reading the EXACT offset
and comparing the EXACT original bytes (ob field) for every instruction.

Operates entirely on in-memory bytes — no file I/O.
Can be used from the CLI, the API layer, or any other caller.

Rules:
- Scans ALL instructions before reporting (never aborts early).
- If ALL match  → safe to patch  (safe_to_patch=True).
- If ANY fail   → full failure report  (safe_to_patch=False). Do not patch.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openremap.core.services.recipes.preflight import (
    check_file_size,
    check_match_key,
    scan_exact_matches,
)
from openremap.core.services.recipes.recipe_builder import check_schema_version


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    instruction_index: int  # 1-based
    offset: int
    offset_hex: str  # derived: f"{offset:X}"
    size: int  # derived: len(bytes.fromhex(ob))
    expected_bytes: str  # hex string — ob field from recipe
    found_bytes: str  # hex string actually read from binary
    passed: bool
    reason: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ECUStrictValidator:
    """
    Validates a target ECU binary against a recipe by checking every
    instruction at its exact recorded offset.

    All input is accepted as in-memory objects — the caller is responsible
    for reading files and parsing JSON before constructing this class.

    Args:
        target_data:  Raw bytes of the target ECU binary.
        recipe:       Parsed recipe dict (format 4.0 — must contain
                      ``instructions`` list with ``offset`` and ``ob`` fields,
                      and optionally an ``ecu`` block with ``file_size`` and
                      ``sw_version``).
        target_name:  Display name used in reports (e.g. original filename).
        recipe_name:  Display name used in reports (e.g. recipe filename).
    """

    def __init__(
        self,
        target_data: bytes,
        recipe: Dict[str, Any],
        target_name: str = "target.bin",
        recipe_name: str = "recipe.json",
    ) -> None:
        self.target_data = target_data
        self.recipe = recipe
        self.target_name = target_name
        self.recipe_name = recipe_name
        self.results: List[ValidationResult] = []

        check_schema_version(recipe)

    # ------------------------------------------------------------------
    # Pre-flight checks
    # ------------------------------------------------------------------

    def check_file_size(self) -> Optional[str]:
        """
        Compare the target size against the recipe's declared file_size.

        Returns:
            An error string if sizes mismatch, None if they match (or if the
            recipe carries no file_size field — treated as a warning, not fatal
            at the service layer; the caller decides how to handle it).
        """
        return check_file_size(self.recipe, len(self.target_data))

    def check_match_key(self) -> Optional[str]:
        """
        Identify the target binary and compare its match_key against the one
        recorded in the recipe.

        Returns:
            A warning string when the match keys differ, None when they match
            (or when either key is absent — treated as unverifiable).
        """
        return check_match_key(self.recipe, self.target_data, self.target_name)

    # ------------------------------------------------------------------
    # Core validation
    # ------------------------------------------------------------------

    def validate_all(self) -> None:
        """
        Iterate every instruction. For each one read exactly ``size`` bytes
        at ``offset`` and compare against ``ob``.
        Populates ``self.results`` — collects ALL results before returning.
        """
        self.results.clear()

        instructions = self.recipe.get("instructions", [])
        file_len = len(self.target_data)

        for outcome in scan_exact_matches(self.target_data, instructions, "ob"):
            if outcome.kind == "ok":
                reason = "Exact match."
            elif outcome.kind == "malformed":
                reason = (
                    f"Invalid hex in 'ob' field: '{outcome.expected}'. "
                    "The recipe is malformed and cannot be validated."
                )
            elif outcome.kind == "bounds":
                reason = (
                    f"Offset 0x{outcome.offset_hex} + {outcome.size} bytes "
                    f"exceeds file length ({file_len:,} bytes)."
                )
            else:
                reason = (
                    f"Value mismatch at 0x{outcome.offset_hex}. "
                    f"Expected {outcome.expected}, found {outcome.found}."
                )

            self.results.append(
                ValidationResult(
                    instruction_index=outcome.instruction_index,
                    offset=outcome.offset,
                    offset_hex=outcome.offset_hex,
                    size=outcome.size,
                    expected_bytes=outcome.expected,
                    found_bytes=outcome.found,
                    passed=outcome.passed,
                    reason=reason,
                )
            )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self) -> tuple[int, int, float]:
        """Returns (passed, failed, score_pct)."""
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        pct = (passed / len(self.results) * 100) if self.results else 0.0
        return passed, failed, pct

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialise the full validation report as a plain dict — ready for a
        JSON response.
        """
        passed, failed, pct = self.score()

        return {
            "target_file": self.target_name,
            "recipe_file": self.recipe_name,
            "target_md5": hashlib.md5(self.target_data).hexdigest(),
            "summary": {
                "total": len(self.results),
                "passed": passed,
                "failed": failed,
                "score_pct": round(pct, 2),
                "safe_to_patch": failed == 0,
            },
            "failures": [
                {
                    "instruction_index": r.instruction_index,
                    "offset": r.offset,
                    "offset_hex": r.offset_hex,
                    "size": r.size,
                    "ob": r.expected_bytes,
                    "found_bytes": r.found_bytes,
                    "reason": r.reason,
                }
                for r in self.results
                if not r.passed
            ],
            "all_results": [
                {
                    "instruction_index": r.instruction_index,
                    "offset": r.offset,
                    "offset_hex": r.offset_hex,
                    "size": r.size,
                    "passed": r.passed,
                    "ob": r.expected_bytes,
                    "found_bytes": r.found_bytes,
                    "reason": r.reason,
                }
                for r in self.results
            ],
        }
