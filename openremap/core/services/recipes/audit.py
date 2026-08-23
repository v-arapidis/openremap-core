"""
Tune audit — the receipt check of the recipe ecosystem.

Given ``stock.bin``, ``tuned.bin`` and a ``.remap`` recipe, answer
**"do these three actually belong together?"** with three verdicts:

1. **Provenance** — does the recipe's ``ecu.sha256`` match the stock
   binary?  (Was the recipe built from THIS original?)
2. **Fingerprint** — re-cook stock vs tuned and compare fingerprints.
   (Is the recipe the honest record of this tune pair — nothing more,
   nothing less?)  The fingerprint covers ONLY the instruction content
   ``(offset, ob, mb)`` — metadata, creator timestamps, and the ``maps``
   annotation layer are excluded by design, so recipes stay comparable
   across metadata churn and future map-annotation improvements.  The
   one honest caveat: a future change to the diff engine's block
   segmentation could re-cook the same pair into different instruction
   boundaries (then old recipes no longer fingerprint-match — reported,
   never a crash).
3. **Unaccounted changes** — apply the recipe to stock and diff the
   predicted output against the actual tuned file.  Every differing byte
   is a change the recipe does NOT explain (hidden edits, flags,
   checksums).  The layout segmenter labels the regions they live in.

Honest limits: this is a consistency check between three artifacts, not a
safety verdict.  Applicability to a different software revision is
``validate before``'s job, not the audit's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from openremap.core.services.maps.layout import find_ident_blocks, segment
from openremap.core.services.recipes.patcher import ECUPatcher
from openremap.core.services.recipes.recipe_builder import (
    ECUDiffAnalyzer,
    check_schema_version,
    compute_fingerprint,
)
from openremap.core.services.recipes.volatile import classify_volatile


@dataclass(frozen=True)
class UnaccountedBlock:
    """A byte range in the tuned file that the recipe does not explain."""

    offset: int
    size: int
    region_kind: str  # layout label: calibration / code / erased / ident / mixed
    region_confidence: float


@dataclass
class AuditResult:
    """The three verdicts plus supporting detail."""

    stock_file: str
    tuned_file: str
    recipe_file: str

    # 1 — provenance
    recipe_sha256: str | None = None
    stock_sha256: str = ""
    provenance_ok: bool = False

    # 2 — fingerprint
    recipe_fingerprint: str | None = None
    recomputed_fingerprint: str = ""
    fingerprint_ok: bool = False
    instruction_count: int = 0
    volatile_recipe: bool = False  # recipe carries a ``volatile`` section

    # 3 — unaccounted changes
    unaccounted_blocks: list[UnaccountedBlock] = field(default_factory=list)
    unaccounted_bytes: int = 0

    warnings: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when all three verdicts pass and nothing is unexplained."""
        return (
            self.provenance_ok
            and self.fingerprint_ok
            and not self.unaccounted_blocks
        )


def _diff_ranges(a: bytes, b: bytes) -> list[tuple[int, int]]:
    """Byte ranges where *a* and *b* differ, merged when closer than 16
    bytes apart (same merge policy as the cook diff)."""
    if len(a) != len(b):
        raise ValueError("binary sizes differ")
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(len(a)):
        if a[i] != b[i]:
            if start is None:
                start = i
        else:
            if start is not None:
                ranges.append((start, i))
                start = None
    if start is not None:
        ranges.append((start, len(a)))

    merged: list[tuple[int, int]] = []
    for s, e in ranges:
        if merged and s - merged[-1][1] <= 16:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def _region_kind_for(regions, offset: int) -> tuple[str, float]:
    for r in regions:
        if r.start <= offset < r.end:
            return r.kind, r.confidence
    return "unknown", 0.0


def _is_volatile_recipe(recipe: dict) -> bool:
    """A recipe is volatile when it carries a ``volatile`` dict section
    (schema 4.5, produced by ``cook-volatile``)."""
    return isinstance(recipe.get("volatile"), dict)


def _excluded_ranges(recipe: dict) -> list[tuple[int, int]]:
    """Declared volatile-exclusion byte ranges as ``(start, end)``
    half-open intervals, sorted and merged."""
    raw: list[tuple[int, int]] = []
    for f in recipe.get("volatile", {}).get("excluded", []):
        start = int(f["offset"])
        end = start + int(f["size"])
        raw.append((start, end))
    raw.sort()
    merged: list[tuple[int, int]] = []
    for s, e in raw:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _subtract_ranges(
    ranges: list[tuple[int, int]], cuts: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Remove the *cuts* intervals (sorted, non-overlapping) from *ranges*."""
    if not cuts:
        return ranges
    out: list[tuple[int, int]] = []
    for s, e in ranges:
        cursor = s
        for cs, ce in cuts:
            if ce <= cursor:
                continue
            if cs >= e:
                break
            if cs > cursor:
                out.append((cursor, cs))
            cursor = max(cursor, ce)
            if cursor >= e:
                break
        if cursor < e:
            out.append((cursor, e))
    return out


def _inst_key(inst: dict) -> tuple[int, str, str]:
    """Canonical instruction identity for subset comparison."""
    return (
        int(inst["offset"]),
        str(inst.get("ob", "")).upper(),
        str(inst.get("mb", "")).upper(),
    )


def audit(
    stock_data: bytes,
    tuned_data: bytes,
    recipe: dict,
    *,
    stock_name: str = "stock.bin",
    tuned_name: str = "tuned.bin",
    recipe_name: str = "recipe.remap",
) -> AuditResult:
    """
    Run the three-way consistency audit.

    Raises:
        ValueError: on unsupported recipe schema, size mismatch between
                    the two binaries, or when the recipe cannot be applied
                    to the stock (patch failure).
    """
    check_schema_version(recipe)

    if len(stock_data) != len(tuned_data):
        raise ValueError(
            f"stock ({len(stock_data):,} B) and tuned ({len(tuned_data):,} B) "
            "have different sizes — they cannot be images of the same ECU."
        )

    result = AuditResult(
        stock_file=stock_name,
        tuned_file=tuned_name,
        recipe_file=recipe_name,
    )

    # ── 1. Provenance ──────────────────────────────────────────────────
    result.recipe_sha256 = recipe.get("ecu", {}).get("sha256")
    result.stock_sha256 = hashlib.sha256(stock_data).hexdigest()
    result.provenance_ok = (
        result.recipe_sha256 is not None
        and result.recipe_sha256 == result.stock_sha256
    )

    # ── 2. Fingerprint ─────────────────────────────────────────────────
    result.instruction_count = len(recipe.get("instructions", []))
    result.recipe_fingerprint = recipe.get("fingerprint")
    result.volatile_recipe = _is_volatile_recipe(recipe)
    # require_unique=False: the fingerprint covers offset/ob/mb only, and
    # the audit must not abort on non-unique anchors (that's the patcher's
    # concern, surfaced separately by validate).
    recomputed_recipe = ECUDiffAnalyzer(
        original_data=stock_data,
        modified_data=tuned_data,
        original_filename=stock_name,
        modified_filename=tuned_name,
        require_unique=False,
    ).build_recipe()
    result.recomputed_fingerprint = recomputed_recipe["fingerprint"]

    if not result.volatile_recipe:
        # Exact-equality — 4.3/4.4 recipes must be the exact honest record.
        result.fingerprint_ok = (
            result.recipe_fingerprint == result.recomputed_fingerprint
        )
    else:
        # Volatile recipe (schema 4.5): the recipe is ALLOWED to be a
        # subset of the full diff (near-certain volatile instructions were
        # deliberately excluded at cook time).  Check four things:
        #   1. self-consistency — the stored fingerprint matches the kept
        #      instruction set it claims to describe;
        #   2. subset — every kept instruction is part of the re-cooked
        #      diff (nothing fabricated, nothing extra);
        #   3. re-verify — every declared excluded offset still classifies
        #      as volatile against the stock (no false exclusions);
        #   4. the excluded set is non-empty (a volatile recipe that
        #      excludes nothing is just a normal recipe).
        full = {_inst_key(i) for i in recomputed_recipe.get("instructions", [])}
        kept = {_inst_key(i) for i in recipe.get("instructions", [])}
        self_consistent = result.recipe_fingerprint == compute_fingerprint(
            recipe.get("instructions", [])
        )
        subset_ok = kept <= full

        report = classify_volatile(
            recomputed_recipe, stock_data, exclude_uncertain=True
        )
        reclassified = {(f.offset, f.size) for f in report.excluded}
        declared = {
            (int(f["offset"]), int(f["size"]))
            for f in recipe.get("volatile", {}).get("excluded", [])
        }
        reverify_ok = declared <= reclassified and bool(declared)

        result.fingerprint_ok = self_consistent and subset_ok and reverify_ok

        if not self_consistent:
            result.warnings.append(
                "fingerprint mismatch — the recipe's stored fingerprint "
                "does not match its own kept instruction set."
            )
        if not subset_ok:
            result.warnings.append(
                "subset mismatch — the volatile recipe's kept instructions "
                "are not a subset of the stock→tuned diff (extra or "
                "fabricated instructions)."
            )
        if declared and not (declared <= reclassified):
            result.warnings.append(
                "volatile re-verify failed — one or more excluded offsets "
                "no longer classify as volatile against the stock."
            )
        if not declared:
            result.warnings.append(
                "volatile re-verify skipped — the recipe declares no "
                "excluded instructions (treat it as a normal recipe)."
            )

    # Verdict warnings must be recorded before any early return.
    if not result.provenance_ok:
        result.warnings.append(
            "provenance mismatch — the recipe was built from a different "
            "original (ecu.sha256 differs from the stock's hash)."
        )
    if not result.fingerprint_ok:
        result.warnings.append(
            "fingerprint mismatch — the recipe's instructions do not "
            "exactly describe the stock→tuned diff."
        )

    # ── 3. Unaccounted changes ─────────────────────────────────────────
    # Predicted tuned file = recipe applied to stock.  When the recipe
    # cannot apply (wrong stock, corrupted instructions), verdict 3 is
    # skipped with a warning — the audit reports, it does not crash.
    try:
        patched = ECUPatcher(stock_data, recipe).apply_all()
    except ValueError as exc:
        result.warnings.append(
            f"unaccounted-changes check skipped — the recipe does not "
            f"apply to this stock ({exc})."
        )
        return result
    if patched is None:
        result.warnings.append(
            "unaccounted-changes check skipped — the recipe failed to "
            "apply to the stock binary."
        )
        return result
    predicted = bytes(patched)

    layout_regions = segment(tuned_data)  # full segment for region labels
    ident_blocks = find_ident_blocks(tuned_data)

    # For volatile recipes, the excluded byte ranges are deliberate — they
    # were removed from the patch list at cook time with recorded evidence,
    # so the predicted-vs-tuned diff at those offsets is expected, not
    # "unaccounted".  Subtract them before reporting.
    excluded = _excluded_ranges(recipe) if result.volatile_recipe else []

    for s, e in _subtract_ranges(_diff_ranges(predicted, tuned_data), excluded):
        # Overlay ident blocks (exact ranges) on top of sector regions.
        kind, conf = "ident", 0.5
        if not any(b.start <= s < b.end for b in ident_blocks):
            kind, conf = _region_kind_for(layout_regions, s)
        result.unaccounted_blocks.append(
            UnaccountedBlock(
                offset=s, size=e - s, region_kind=kind, region_confidence=conf
            )
        )
    result.unaccounted_bytes = sum(b.size for b in result.unaccounted_blocks)

    return result
