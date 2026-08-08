"""
Recipe instruction annotator — flag suspicious changes.

The diff engine captures every changed byte between two ECU binaries.
That includes calibration map changes (desired) AND volatile / vehicle-
specific data like VIN numbers, checksums, IMMO blocks, etc.

This module scans instructions and attaches non-destructive flags to
anything that looks suspicious.  Nothing is removed — the user decides.

Flags
-----
Each flag is a dict with:
    kind        — tag: VIN_SUSPECT, WEAK_ANCHOR, LOW_ENTROPY_CTX, …
    reason      — human-readable explanation
    confidence  — float 0.0–1.0 (0.9 = high, 0.5 = medium, 0.3 = low)
    action      — always "REVIEW" (we never auto-remove)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Protocol


# ---------------------------------------------------------------------------
# Flag dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstructionFlag:
    """A single flag attached to a recipe instruction."""

    kind: str
    reason: str
    confidence: float  # 0.0–1.0 (0.9 = high, 0.5 = medium, 0.3 = low)
    action: str = "REVIEW"

    def to_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "confidence": self.confidence,
            "action": self.action,
        }


# ---------------------------------------------------------------------------
# Scanner protocol
# ---------------------------------------------------------------------------


class InstructionScanner(Protocol):
    """Interface for pluggable instruction scanners."""

    def scan(
        self,
        instruction: Dict,
        original_data: bytes,
    ) -> List[InstructionFlag]:
        """
        Examine one instruction and return zero or more flags.

        Args:
            instruction: Single instruction dict from the recipe
                         (has offset, size, ob, mb, ctx, etc.)
            original_data: The full original binary (for context lookups
                          beyond what's in the instruction itself).

        Returns:
            List of InstructionFlag instances.  Empty means clean.
        """
        ...


# ---------------------------------------------------------------------------
# VIN scanner
# ---------------------------------------------------------------------------

# ISO 3779 VIN: 17 characters, A-Z 0-9 excluding I, O, Q
_VIN_CHARSET = b"ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
_VIN_RE = re.compile(rb"[A-HJ-NPR-Z0-9]{17}")

# Minimum context window around instruction to search for VINs.
# A VIN is 17 bytes; the instruction might only overlap part of it.
_VIN_SCAN_MARGIN = 24


class VINScanner:
    """
    Detect instructions that overlap with a VIN-shaped byte sequence
    in the original binary.

    Strategy:
        1. Look at the region of the original binary around the
           instruction's offset (offset - margin .. offset + size + margin).
        2. Search that region for any 17-byte sequence matching the
           ISO 3779 VIN character set.
        3. If the instruction's byte range overlaps with a VIN hit,
           flag it.

    This checks the ORIGINAL binary — if there's a VIN-shaped string
    in the original at/near the instruction offset, and the instruction
    changes bytes in that region, it's suspicious.
    """

    def scan(
        self,
        instruction: Dict,
        original_data: bytes,
    ) -> List[InstructionFlag]:
        flags: List[InstructionFlag] = []

        offset = instruction["offset"]
        size = instruction["size"]
        inst_start = offset
        inst_end = offset + size

        # Widen the search window so we catch VINs that partially overlap
        scan_start = max(0, offset - _VIN_SCAN_MARGIN)
        scan_end = min(len(original_data), offset + size + _VIN_SCAN_MARGIN)
        window = original_data[scan_start:scan_end]

        for m in _VIN_RE.finditer(window):
            vin_abs_start = scan_start + m.start()
            vin_abs_end = scan_start + m.end()

            # Check overlap: instruction range [inst_start, inst_end)
            #                 VIN range [vin_abs_start, vin_abs_end)
            if inst_start < vin_abs_end and vin_abs_start < inst_end:
                try:
                    vin_str = m.group(0).decode("ascii")
                except UnicodeDecodeError:
                    vin_str = m.group(0).hex().upper()

                flags.append(
                    InstructionFlag(
                        kind="VIN_SUSPECT",
                        reason=(
                            f"Instruction overlaps with VIN-shaped string "
                            f"'{vin_str}' at 0x{vin_abs_start:X}\u20130x{vin_abs_end:X}"
                        ),
                        confidence=0.9,
                    )
                )
                # One VIN flag per instruction is enough
                break

        return flags


# ---------------------------------------------------------------------------
# Low-entropy context scanner
# ---------------------------------------------------------------------------

# Default entropy threshold — same as find_unique_context().
_LOW_ENTROPY_THRESHOLD = 2.5


class LowEntropyScanner:
    """
    Detect instructions whose context anchor is weak — low entropy, non-unique,
    or both.

    The recipe builder already computes ctx_entropy and ctx_unique for every
    instruction via find_unique_context().  This scanner simply reads those
    fields and attaches a human-visible flag when the anchor is below threshold.

    Two cases:
      1. Non-unique anchor (ctx_unique == False) — the ctx+ob pattern appears
         more than once even in the ORIGINAL binary.  This means Force Save
         was used to bypass Guard 3, and the resulting tune may not apply
         reliably to other binaries.  Flagged as WEAK_ANCHOR / HIGH.
      2. Low-entropy but unique (ctx_entropy < threshold, ctx_unique == True) —
         the anchor is in a repetitive/padding region.  It's unique in the
         original, but fragile — a different SW revision with different padding
         could break it.  Flagged as LOW_ENTROPY_CTX / LOW.
    """

    def __init__(self, entropy_threshold: float = _LOW_ENTROPY_THRESHOLD) -> None:
        self.entropy_threshold = entropy_threshold

    def scan(
        self,
        instruction: Dict,
        original_data: bytes,
    ) -> List[InstructionFlag]:
        flags: List[InstructionFlag] = []

        entropy = instruction.get("ctx_entropy", None)
        is_unique = instruction.get("ctx_unique", None)
        context_size = instruction.get("context_size", 0)

        # Case 1: Non-unique — anchor matches multiple times in original binary.
        #          This is the dangerous one; only possible via Force Save.
        if is_unique is False:
            match_count_hint = ""
            # context_size of 0 or 1 means no usable anchor — degenerate case
            if context_size <= 1:
                match_count_hint = (
                    f" (only {context_size} byte(s) of context available "
                    f"before offset — anchor is degenerate)"
                )
            flags.append(
                InstructionFlag(
                    kind="WEAK_ANCHOR",
                    reason=(
                        f"ctx+ob pattern is non-unique in the original binary "
                        f"(entropy={entropy:.1f}, context_size={context_size}). "
                        f"This tune was force-saved and may not apply reliably "
                        f"to a different ECU or SW revision."
                        + match_count_hint
                    ),
                    confidence=0.9,
                )
            )
            return flags  # Don't add a second flag for the same root cause.

        # Case 2: Unique but low-entropy — anchor is in padding/repetitive region.
        #          Fragile across SW revisions but fine for same-binary use.
        if entropy is not None and entropy < self.entropy_threshold:
            flags.append(
                InstructionFlag(
                    kind="LOW_ENTROPY_CTX",
                    reason=(
                        f"Context anchor entropy is {entropy:.1f} bits/byte "
                        f"(below {self.entropy_threshold}). "
                        f"The anchor is unique in the original binary but "
                        f"sits in a repetitive region — a different SW revision "
                        f"may break the match."
                    ),
                    confidence=0.3,
                )
            )

        return flags


# ---------------------------------------------------------------------------
# Recipe annotator — runs all scanners
# ---------------------------------------------------------------------------


class RecipeAnnotator:
    """
    Run all registered scanners over a recipe's instructions and
    attach flags.

    Usage::

        annotator = RecipeAnnotator()
        # optionally: annotator.add_scanner(MyCustomScanner())
        annotator.annotate(recipe, original_data)
        # recipe["instructions"][i]["flags"] is now populated
    """

    def __init__(self) -> None:
        self._scanners: List[InstructionScanner] = [
            VINScanner(),
            LowEntropyScanner(),
        ]

    def add_scanner(self, scanner: InstructionScanner) -> None:
        """Register an additional scanner."""
        self._scanners.append(scanner)

    def annotate(
        self,
        recipe: Dict,
        original_data: bytes,
    ) -> Dict:
        """
        Annotate every instruction in the recipe with flags.

        Modifies the recipe dict in-place and returns it.
        Each instruction gets a ``flags`` key (list of flag dicts).
        Instructions with no issues get an empty list.
        """
        for instruction in recipe.get("instructions", []):
            all_flags: List[InstructionFlag] = []
            for scanner in self._scanners:
                all_flags.extend(scanner.scan(instruction, original_data))
            instruction["flags"] = [f.to_dict() for f in all_flags]

        return recipe

    def flagged_count(self, recipe: Dict) -> int:
        """Return the number of instructions that have at least one flag."""
        return sum(1 for inst in recipe.get("instructions", []) if inst.get("flags"))

    def flag_summary(self, recipe: Dict) -> List[str]:
        """
        Return a list of human-readable summary lines for all flagged
        instructions.  Empty if no flags.
        """
        lines: List[str] = []
        for inst in recipe.get("instructions", []):
            for flag in inst.get("flags", []):
                offset_hex = inst.get("offset_hex", f"{inst['offset']:X}")
                lines.append(
                    f"0x{offset_hex} — {flag['kind']} ({flag['confidence']}): "
                    f"{flag['reason']}"
                )
        return lines
