"""
ECU Recipe Builder — .openremap format

Accepts two ECU binary files as raw bytes (in-memory), compares them and
produces a .openremap recipe consumed by the patcher pipeline.

Instruction fields emitted:
    offset          — absolute byte offset in the original file (int)
    ob              — original bytes at that offset (hex, uppercase)
    mb              — modified bytes to write (hex, uppercase)
    ctx             — context_before bytes used as anchor (hex, uppercase)
    size            — number of bytes (int, derived — convenience only)
    offset_hex      — offset as hex string (derived — convenience only)
    description     — human-readable summary
    flags           — list of annotation flags (VIN_SUSPECT, etc.)

ECU identification is fully delegated to identifier.py — this file
contains only the diff engine and .openremap recipe assembly.

The ecu block embedded in the recipe contains only the lean identity fields:
    manufacturer, match_key, ecu_family, ecu_variant,
    software_version, hardware_number, file_size, sha256.

Safety guards
-------------
build_recipe() enforces two checks before diffing:

  1. SIZE MATCH (hard error)
     The original and modified binaries must be exactly the same size.
     If they differ, ValueError is raised immediately — no diff is run.
     Rationale: ECU flash images are fixed-size. A size mismatch almost
     always means two different ECU models or a corrupted file.  Diffing
     binaries of different sizes would silently discard the tail of the
     larger file, producing wrong offsets for every instruction.

  2. IDENTITY MATCH (warning, not fatal)
     Both binaries are identified independently.  If their match_keys
     differ the recipe is still built but cook_warnings() returns a
     human-readable warning string.  The recipe's ecu block contains a
     new field ``cook_warnings`` listing any issues found at build time
     so downstream tools can surface them.
     Rationale: cooking ME7.5 vs EDC17 is almost certainly a mistake,
     but there are legitimate edge cases (anonymised bins, unknown ECUs)
     where identification fails on one side — we warn rather than block.

  ⚠  RAW DIFF WARNING
     find_changes() is a raw byte comparison of the ENTIRE binary.
     It captures calibration map changes AND any other byte that differs
     between the two files, including:
       - ECU checksums corrected by WinOLS / Alientech / etc.
       - VIN numbers stored in flash
       - Immobilizer (IMMO) data
       - ECU serial numbers
       - Odometer counters
     Always review the instruction list before applying a recipe.
     Checksum instructions must be removed and the checksum recalculated
     by a professional tool (WinOLS, ECM Titanium, etc.) after patching.
Recipe provenance
-----------------
Every recipe embeds:

  - ``creator`` block — tool name, version, timestamp, optional author
  - ``fingerprint`` — SHA-256 of the instruction content (offset + ob + mb)
  - ``trust_level`` — UNSIGNED | COMMUNITY | SIGNED | VERIFIED

The fingerprint is NOT tamper protection on its own.  It is a
deduplication and corruption-detection tool.  Tamper protection
requires a digital signature (future feature).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from openremap.core.services.identifier import identify_ecu
from openremap.core.services.entropy import find_unique_context

import hashlib
import json
from datetime import datetime, timezone

from openremap.core.services.annotator import RecipeAnnotator


# ---------------------------------------------------------------------------
# Change dataclass
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """Represents a single change block between two ECU binaries."""

    offset: int
    size: int
    ob: str  # original bytes — hex, uppercase
    mb: str  # modified bytes — hex, uppercase
    ctx: str  # context_before bytes — hex, uppercase
    context_after: str
    context_size: int
    # Phase 1 entropy-gated context fields
    ctx_entropy: float = 0.0  # Shannon entropy of ctx bytes
    ctx_unique_in_original: int = 1  # 1 = unique, >1 = ambiguous in original binary
    ctx_expanded: bool = False  # True when context was expanded beyond min_size

    @property
    def offset_hex(self) -> str:
        return f"{self.offset:X}"

    def to_dict(self) -> Dict:
        return {
            "offset": self.offset,
            "offset_hex": self.offset_hex,
            "size": self.size,
            "ob": self.ob,
            "mb": self.mb,
            "ctx": self.ctx,
            "context_after": self.context_after,
            "context_size": self.context_size,
            "ctx_entropy": self.ctx_entropy,
            "ctx_unique": self.ctx_unique_in_original == 1,
            "ctx_expanded": self.ctx_expanded,
            "description": self._description(),
        }

    def _description(self) -> str:
        if self.size == 1:
            return f"Byte at 0x{self.offset_hex}: 0x{self.ob} -> 0x{self.mb}"
        return f"{self.size} bytes at 0x{self.offset_hex} modified"


# ---------------------------------------------------------------------------
# Trust & fingerprint helpers
# ---------------------------------------------------------------------------


def compute_fingerprint(instructions: list[dict]) -> str:
    """
    Deterministic SHA-256 fingerprint of the instruction content.

    Computed from a canonical representation of (offset, ob, mb) tuples
    sorted by offset.  Same tune = same fingerprint, always — regardless
    of who created it, when, or what metadata they added.

    This is NOT tamper protection on its own (anyone can recompute the
    hash).  It becomes tamper-proof only when combined with a digital
    signature (future feature).

    Uses:
        - Deduplication: two recipes with the same fingerprint are the
          same tune.
        - Accidental corruption detection: if the file was garbled, the
          fingerprint won't match a recomputation.
    """
    canonical = sorted(
        (inst["offset"], inst["ob"], inst["mb"]) for inst in instructions
    )
    blob = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(blob).hexdigest()}"


def derive_trust_level(creator: dict) -> str:
    """
    Derive the trust level from the creator block.

    UNSIGNED   — no name
    COMMUNITY  — name present, no signature
    SIGNED     — name + valid signature (future)
    VERIFIED   — signed + platform-verified identity (future)
    """
    name = creator.get("name")
    signature = creator.get("signature")
    verified = creator.get("id") is not None  # stable ID = platform-verified (future)

    if signature and name and verified:
        return "VERIFIED"
    if signature and name:
        return "SIGNED"
    if name:
        return "COMMUNITY"
    return "UNSIGNED"


def build_creator_block(
    name: str | None = None,
    handle: str | None = None,
    id: str | None = None,
) -> dict:
    """
    Build the creator metadata block.

    Args:
        name: Display name. None = anonymous.
        handle: Optional handle (GitHub, Discord, etc.).
        id: Optional stable user ID for provenance.

    Returns:
        Creator dict ready to embed in the recipe.
    """
    creator: dict = {
        "name": name or "",
        "handle": handle or "",
        "id": id or "",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signature": None,
    }
    creator["trust_level"] = derive_trust_level(creator)
    return creator


# ---------------------------------------------------------------------------
# Schema version gate
# ---------------------------------------------------------------------------

# Minimum recipe schema version accepted by the patcher, validators, and
# all other consumers.  Recipes older than this are rejected with a clear
# error — the format changed significantly at 4.3 (flattened envelope,
# renamed fields, new required fields).
#
# Future minor versions (4.4, 4.5, …) pass through because the spec
# requires all parsers to ignore unknown fields.  A major version bump
# (5.0) will need its own gate.
_MIN_RECIPE_SCHEMA = (4, 3)


def check_schema_version(recipe: dict) -> None:
    """
    Validate that *recipe* carries a supported ``schema_version``.

    Raises ``ValueError`` for recipes older than 4.3 (pre-0.5.0 format).
    Recipes with ``schema_version >= 4.3`` pass through — future minor
    versions are forward-compatible by the extensibility rule.

    Call this once at the entry point of every service that consumes a
    recipe dict (patcher, validators, API handlers).
    """
    raw = recipe.get("schema_version") or recipe.get("format_version")
    if raw is None:
        raise ValueError(
            "Unsupported recipe format: no schema_version field. "
            "Recipes produced by openremap < 0.5.0 (format 4.2 and earlier) "
            "are not compatible with this version. "
            "Please re-cook the recipe with openremap >= 0.5.0."
        )

    try:
        parts = tuple(int(p) for p in str(raw).split("."))
    except (ValueError, AttributeError):
        raise ValueError(
            f"Unsupported recipe schema_version: {raw!r}. "
            f"Expected a semver-style version string (e.g. '4.3')."
        )

    if len(parts) < 2:
        raise ValueError(
            f"Unsupported recipe schema_version: {raw!r}. "
            f"Expected major.minor format (e.g. '4.3')."
        )

    if parts < _MIN_RECIPE_SCHEMA:
        raise ValueError(
            f"Unsupported recipe schema_version: {raw}. "
            f"Minimum supported version is {_MIN_RECIPE_SCHEMA[0]}.{_MIN_RECIPE_SCHEMA[1]}. "
            f"Recipes in format {raw} must be re-cooked with openremap >= 0.5.0."
        )


# ---------------------------------------------------------------------------
# ECUDiffAnalyzer
# ---------------------------------------------------------------------------


class ECUDiffAnalyzer:
    """
    Analyzes differences between two ECU binary files and produces a
    .openremap recipe — the same format consumed by the patcher pipeline.

    Operates entirely on in-memory bytes — no file I/O.
    Manufacturer identification is delegated to the registry.
    """

    def __init__(
        self,
        original_data: bytes,
        modified_data: bytes,
        original_filename: str,
        modified_filename: str,
        context_size: int = 32,
        max_context_size: int = 512,
        entropy_threshold: float = 2.5,
        require_unique: bool = True,
        author: dict | None = None,
    ) -> None:
        self.original_data = original_data
        self.modified_data = modified_data
        self.original_filename = original_filename
        self.modified_filename = modified_filename
        self.context_size = context_size
        self.max_context_size = max_context_size
        self.entropy_threshold = entropy_threshold
        self.require_unique = require_unique
        self.changes: List[Change] = []
        self._cook_warnings: List[str] = []
        self.author = author

    # -----------------------------------------------------------------------
    # Pre-cook guards
    # -----------------------------------------------------------------------

    def check_size_match(self) -> Optional[str]:
        """
        Verify that both binaries are exactly the same size.

        ECU flash images are fixed-size.  A mismatch almost always means
        two different ECU models or a corrupted/truncated file.  Diffing
        binaries of different sizes silently discards the tail of the
        larger file, producing wrong offsets for every instruction.

        Returns:
            An error string if the sizes differ, None if they match.
        """
        orig_size = len(self.original_data)
        mod_size = len(self.modified_data)
        if orig_size != mod_size:
            return (
                f"File size mismatch: original is {orig_size:,} bytes, "
                f"modified is {mod_size:,} bytes. "
                "Both files must be the same size — they must be images of "
                "the same ECU model. If the sizes differ you are most likely "
                "comparing two different ECU families or a corrupted file."
            )
        return None

    def check_identity_match(self) -> Optional[str]:
        """
        Identify both binaries independently and compare their match_keys.

        A mismatch means the two files are from different ECU families or
        software revisions — cooking a recipe from them would produce
        instructions that make no sense when applied to either binary.

        Returns:
            A warning string if the identities differ or cannot be
            compared, None if both match_keys are equal.
            Returns None (silently) when identification fails on either
            side — unknown binaries cannot be compared.
        """
        try:
            orig_id = identify_ecu(
                data=self.original_data, filename=self.original_filename
            )
            mod_id = identify_ecu(
                data=self.modified_data, filename=self.modified_filename
            )
        except Exception:
            return None  # identification failed — cannot compare, do not block

        orig_key = orig_id.get("match_key")
        mod_key = mod_id.get("match_key")

        # If either side is unidentified we cannot make a meaningful comparison
        if not orig_key or not mod_key:
            return None

        if orig_key != mod_key:
            orig_family = orig_id.get("ecu_family") or "unknown"
            mod_family = mod_id.get("ecu_family") or "unknown"
            return (
                f"ECU identity mismatch: original identifies as '{orig_key}' "
                f"({orig_family}), modified identifies as '{mod_key}' "
                f"({mod_family}). "
                "You are diffing two different ECU families or SW revisions. "
                "The produced recipe will contain nonsense instructions and "
                "must NOT be applied to any vehicle."
            )
        return None

    def cook_warnings(self) -> List[str]:
        """
        Return the list of non-fatal warnings produced during the last
        build_recipe() call.

        Always call build_recipe() before reading this — the list is
        populated (and cleared) at the start of each build_recipe() call.

        Returns:
            List of human-readable warning strings.  Empty when clean.
        """
        return list(self._cook_warnings)

    # -----------------------------------------------------------------------
    # Diff engine
    # -----------------------------------------------------------------------

    def _get_verified_context(
        self, offset: int, size: int, ob: bytes
    ) -> Tuple[bytes, bytes, float, int, bool]:
        """
        Capture context before ``offset`` and verify it produces a unique
        anchor in the original binary.

        Delegates to ``find_unique_context()`` which expands the context
        window geometrically until the anchor is both high-entropy and
        unique (or ``max_context_size`` is reached).

        Returns:
            ``(context_before, context_after, entropy, match_count, expanded)``

            ``match_count`` is the number of times ``ctx + ob`` appears in
            the original binary — 1 is unique.  ``expanded`` is True when
            the context was expanded beyond the configured ``context_size``
            minimum.
        """
        ctx, actual_size, entropy, match_count = find_unique_context(
            data=self.original_data,
            change_offset=offset,
            change_size=size,
            ob=ob,
            min_size=self.context_size,
            max_size=self.max_context_size,
            entropy_threshold=self.entropy_threshold,
        )
        after_start = offset + size
        after_end = min(len(self.original_data), after_start + self.context_size)
        ctx_after = self.original_data[after_start:after_end]
        expanded = actual_size > self.context_size
        return ctx, ctx_after, entropy, match_count, expanded

    def find_changes(self, merge_threshold: int = 16) -> None:
        """
        Find all changed byte blocks between original and modified.

        Nearby diff positions within merge_threshold bytes of each other
        are merged into a single instruction, reducing total instruction count.
        """
        self.changes.clear()

        min_length = min(len(self.original_data), len(self.modified_data))

        diff_positions = [
            i
            for i in range(min_length)
            if self.original_data[i] != self.modified_data[i]
        ]

        if not diff_positions:
            return

        # Group positions into contiguous blocks
        blocks: List[Tuple[int, int]] = []
        start = diff_positions[0]
        end = diff_positions[0]

        for pos in diff_positions[1:]:
            if pos - end <= merge_threshold:
                end = pos
            else:
                blocks.append((start, end))
                start = pos
                end = pos
        blocks.append((start, end))

        for blk_start, blk_end in blocks:
            size = blk_end - blk_start + 1
            ob_raw = self.original_data[blk_start : blk_end + 1]
            ob = ob_raw.hex().upper()
            mb = self.modified_data[blk_start : blk_end + 1].hex().upper()
            ctx_before, ctx_after, entropy, match_count, expanded = (
                self._get_verified_context(blk_start, size, ob_raw)
            )

            self.changes.append(
                Change(
                    offset=blk_start,
                    size=size,
                    ob=ob,
                    mb=mb,
                    ctx=ctx_before.hex().upper(),
                    context_after=ctx_after.hex().upper(),
                    context_size=len(ctx_before),
                    ctx_entropy=entropy,
                    ctx_unique_in_original=match_count,
                    ctx_expanded=expanded,
                )
            )

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    def compute_stats(self) -> Dict:
        """Return a statistical summary of the diff."""
        if not self.changes:
            return {}

        total_changed = sum(c.size for c in self.changes)
        file_size = len(self.original_data)
        single = sum(1 for c in self.changes if c.size == 1)

        return {
            "total_changes": len(self.changes),
            "total_bytes_changed": total_changed,
            "percentage_changed": round(total_changed / file_size * 100, 4),
            "single_byte_changes": single,
            "multi_byte_changes": len(self.changes) - single,
            "largest_change_size": max(c.size for c in self.changes),
            "smallest_change_size": min(c.size for c in self.changes),
            "min_context_size": self.context_size,
            "max_context_size": self.max_context_size,
        }

    # -----------------------------------------------------------------------
    # Identification
    # -----------------------------------------------------------------------

    def extract_ecu_identifiers(self) -> Dict:
        """
        Extract identifying information from the original binary.
        Delegates entirely to the manufacturer registry.
        """
        return identify_ecu(
            data=self.original_data,
            filename=self.original_filename,
        )

    # -----------------------------------------------------------------------
    # Recipe builder
    # -----------------------------------------------------------------------

    def build_recipe(self, description: str | None = None) -> Dict:
        """
        Build the full .openremap recipe dict.

        Runs two pre-cook guards before diffing:

          1. SIZE MATCH — raises ValueError immediately if the two binaries
             are not the same size.  No diff is run, no recipe is produced.

          2. IDENTITY MATCH — identifies both binaries and compares their
             match_keys.  A mismatch is recorded as a warning (not fatal)
             accessible via cook_warnings() and embedded in the recipe's
             ecu block under ``cook_warnings``.

        Ready to be serialised, stored, or passed directly to the patcher pipeline.
        Consumed directly by: ecu_validate_strict, ecu_validate_exists,
        ecu_validate_patched, ecu_patcher.

        Recipe shape
        ------------
        {
            "openremap": { "type": "recipe", "schema_version": "4.0" },
            "metadata": { ... },
            "ecu": {
                "file_size": int,
                "sw_version": str | None,
                "ecu_family": str | None,
                "ecu_variant": str | None,
                "match_key": str | None,
                "hardware_number": str | None,
                "calibration_id": str | None,
                "cook_warnings": list[str],   # non-empty when guards triggered
                ...full ecu_identification fields...
            },
            "statistics": { ... },
            "instructions": [
                {
                    "offset": int,
                    "offset_hex": str,
                    "size": int,
                    "ob": str,   # original bytes
                    "mb": str,   # modified bytes
                    "ctx": str,  # context_before anchor
                    ...
                },
                ...
            ]
        }

        Raises:
            ValueError: if the two binaries are not the same size.
        """
        self._cook_warnings.clear()

        # --- Guard 1: size match (hard error) ---
        size_error = self.check_size_match()
        if size_error:
            raise ValueError(size_error)

        # --- Guard 2: identity match (warning) ---
        identity_warning = self.check_identity_match()
        if identity_warning:
            self._cook_warnings.append(identity_warning)

        self.find_changes()

        # --- Guard 3: non-unique context anchors ---
        non_unique = [c for c in self.changes if c.ctx_unique_in_original > 1]
        if non_unique:
            if self.require_unique:
                detail = "\n".join(
                    f"  0x{c.offset:08X}: ctx+ob matches {c.ctx_unique_in_original} "
                    f"times in original binary (entropy={c.ctx_entropy:.1f}, "
                    f"context_size={c.context_size})"
                    for c in non_unique
                )
                raise ValueError(
                    f"{len(non_unique)} instruction(s) have non-unique context "
                    f"anchors after expansion to {self.max_context_size} bytes. "
                    f"The original binary contains padding or repetitive regions "
                    f"that make these maps unmatchable with the current context "
                    f"size limit.\n{detail}"
                )
            else:
                for c in non_unique:
                    self._cook_warnings.append(
                        f"Instruction at 0x{c.offset:08X}: ctx+ob anchor is "
                        f"non-unique (matches {c.ctx_unique_in_original} times "
                        f"in original, entropy={c.ctx_entropy:.1f}). "
                        "Apply will be unreliable."
                    )

        ecu_id = self.extract_ecu_identifiers()

        # Build the ecu block — maps to what the patcher services expect
        # (file_size for size checks, software_version for SW revision warnings)
        ecu_block = {
            "manufacturer": ecu_id.get("manufacturer"),
            "match_key": ecu_id.get("match_key"),
            "ecu_family": ecu_id.get("ecu_family"),
            "ecu_variant": ecu_id.get("ecu_variant"),
            "software_version": ecu_id.get("software_version"),
            "hardware_number": ecu_id.get("hardware_number"),
            "calibration_id": ecu_id.get("calibration_id"),
            "oem_part_number": ecu_id.get("oem_part_number"),
            "platform": ecu_id.get("platform"),
            "calibration_version": ecu_id.get("calibration_version"),
            "serial_number": ecu_id.get("serial_number"),
            "dataset_number": ecu_id.get("dataset_number"),
            "file_size": ecu_id.get("file_size"),
            "sha256": ecu_id.get("sha256"),
            "cook_warnings": list(self._cook_warnings),
        }

        instructions = [change.to_dict() for change in self.changes]

        author = self.author or {}
        recipe = {
            "type": "recipe",
            "schema_version": "4.3",
            "source": "full_cook",
            "application": "openremap-core",
            "creator": build_creator_block(
                name=author.get("name"),
                handle=author.get("handle"),
                id=author.get("id"),
            ),
            "fingerprint": compute_fingerprint(instructions),
            "metadata": {
                "name": self.modified_filename,
                "description": description or "",
                "tags": [],
                "instruction_count": len(instructions),
                "original_file": self.original_filename,
                "modified_file": self.modified_filename,
                "original_size": len(self.original_data),
                "modified_size": len(self.modified_data),
                "tune_id": None,
            },
            "ecu": ecu_block,
            "statistics": self.compute_stats(),
            "instructions": instructions,
        }

        # Run annotator — attaches flags to each instruction in-place
        annotator = RecipeAnnotator()
        annotator.annotate(recipe, self.original_data)

        return recipe

    def build_orst(
        self,
        *,
        id: str,
        name: str,
        message: str | None = None,
        source_sha256: str = "",
        source_path_hint: str = "",
        base_tune_id: str | None = None,
        created_at: str | None = None,
        modified_at: str | None = None,
    ) -> dict:
        """
        Build an .orst (saved tune) dict from the diff results.

        Produces a schema-2.0 tune file with the same instruction shape
        as a recipe, but with minimal metadata — just enough for the
        editor to reopen and export.

        Called after ``find_changes()``.  The annotator is NOT run;
        instruction flags are recipe-level metadata, not stored in .orst.
        ``status`` is always ``"Normal"`` — ``Unresolved`` only appears
        after a binary rebase, which happens in the editor, not at cook
        time.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        original_size = len(self.original_data)

        instructions = []
        for change in self.changes:
            inst = change.to_dict()
            inst["flags"] = []
            inst["status"] = "Normal"
            instructions.append(inst)

        return {
            "orst": "2.0",
            "id": id,
            "name": name,
            "message": message,
            "source_binary": {
                "sha256": source_sha256,
                "file_size": original_size,
                "path_hint": source_path_hint,
            },
            "base_tune_id": base_tune_id,
            "created_at": created_at or now,
            "modified_at": modified_at or now,
            "archived_at": None,
            "instructions": instructions,
        }
