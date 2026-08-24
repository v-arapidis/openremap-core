"""
Flash-layout region tagging for recipe instructions — advisory metadata.

After a recipe is built, every instruction is tagged with the
flash-layout region its edit lands in (``calibration`` / ``code`` /
``erased`` / ``mixed``).  Edits outside a calibration region get a
``CODE_AREA`` flag: they are revision-sensitive — calibration tables are
usually portable across revisions of an ECU, code is not.

The tags are **advisory only**: they never filter instructions, never
block a cook, and never change what ``tune`` applies.  The layout itself
is structural inference (no manufacturer database), so a wrong estimate
can only produce a wrong tag or warning — never a wrong recipe.  When the
segmenter finds no calibration signal at all, instructions are tagged
``unknown`` and nothing is flagged (whole-file fallback).
"""

from __future__ import annotations

from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import scan_map_tables

# Region kinds that are NOT calibration — edits there are flagged.
_RISKY_KINDS = frozenset({"code", "erased", "mixed"})

_CODE_AREA_FLAG = {
    "kind": "CODE_AREA",
    "confidence": 1.0,
    "action": "REVIEW",
    "reason": (
        "edit outside the calibration region (code/erased/mixed flash area) — "
        "may not apply to other revisions of this ECU"
    ),
}


def tag_instruction_regions(
    recipe: dict, data: bytes, tables: list | None = None,
) -> dict:
    """Tag each recipe instruction with its flash-layout region.

    Sets ``inst["region"]`` to the region kind containing the
    instruction's offset and appends a ``CODE_AREA`` flag when the edit
    is outside a calibration region.  When the segmenter finds no
    calibration signal the instructions are tagged ``unknown`` and
    nothing is flagged.

    ``tables`` may carry a precomputed ``scan_map_tables`` result (same
    parameters as the internal default) so callers that already scanned
    the stock binary (map annotation) share one scan instead of two.
    The layout scan is ~2.5-3 s on a 4 MB binary — the cost of the
    advisory portability signal.

    Returns a summary dict: ``{"tagged", "risky", "by_region"}``.
    """
    instructions = recipe.get("instructions", [])
    if not instructions:
        return {"tagged": 0, "risky": 0, "by_region": {}}

    if tables is None:
        tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    calibration = [r for r in regions if r.kind == "calibration"]

    def _tagged(insts: list[dict]) -> int:
        return sum(1 for i in insts if i.get("offset") is not None)

    if not calibration:
        for inst in instructions:
            inst["region"] = "unknown"
        return {
            "tagged": _tagged(instructions),
            "risky": 0,
            "by_region": {"unknown": _tagged(instructions)},
        }

    def _region_of(offset: int) -> str:
        for r in regions:
            if r.start <= offset < r.end:
                return r.kind
        return "unknown"

    by_region: dict[str, int] = {}
    risky = 0
    for inst in instructions:
        offset = inst.get("offset")
        if offset is None:
            continue
        kind = _region_of(offset)
        inst["region"] = kind
        by_region[kind] = by_region.get(kind, 0) + 1
        if kind in _RISKY_KINDS:
            risky += 1
            flags = inst.setdefault("flags", [])
            if not any(f.get("kind") == "CODE_AREA" for f in flags):
                flags.append(dict(_CODE_AREA_FLAG))

    return {"tagged": _tagged(instructions), "risky": risky, "by_region": by_region}
