"""
openremap.core.manufacturers.scan — batch classification of ECU binaries.

The bytes→``ScanResult`` classify core shared by the CLI batch scanner and the
TUI: run every registered extractor (unlike the live identifier, which stops
at the first match) so contested files can be detected and routed.  Pure
bytes-in / dataclass-out; the directory-traversal ``scan`` RPC handler stays
in the API gate.
"""

from __future__ import annotations

from typing import Optional

from openremap.core.manufacturers import EXTRACTORS
from openremap.core.manufacturers.base import BaseManufacturerExtractor

# .hex is accepted because Subaru (Denso/Hitachi) dumps conventionally ship
# as raw binaries named ".hex" (RomRaider convention) — not Intel HEX text.
VALID_EXTENSIONS = {".bin", ".ori", ".hex", ".s19", ".srec", ".mot"}

DEST_SCANNED = "scanned"
DEST_SW_MISSING = "sw_missing"
DEST_CONTESTED = "contested"
DEST_UNKNOWN = "unknown"
DEST_TRASH = "trash"

ALL_DEST = [DEST_SCANNED, DEST_SW_MISSING, DEST_CONTESTED, DEST_UNKNOWN, DEST_TRASH]

ORGANIZABLE_DEST = {DEST_SCANNED, DEST_SW_MISSING}


class ScanResult:
    """Holds the outcome of running all extractors against one file."""

    __slots__ = ("claimants", "extractor", "extraction", "destination", "detail")

    def __init__(
        self,
        claimants: list[BaseManufacturerExtractor],
        extractor: Optional[BaseManufacturerExtractor],
        extraction: Optional[dict],
        destination: str,
        detail: str,
    ) -> None:
        self.claimants = claimants
        self.extractor = extractor
        self.extraction = extraction
        self.destination = destination
        self.detail = detail


def classify_file(data: bytes, filename: str) -> ScanResult:
    """
    Run every registered extractor against data.

    Unlike the live identifier (which stops at the first match), all extractors
    are run so contested files can be detected.

    Routing rules:
      - No claimants              → unknown
      - More than one claimant    → contested
      - Exactly one claimant,
        match_key is None         → sw_missing
      - Exactly one claimant,
        match_key present         → scanned
    """
    claimants: list[BaseManufacturerExtractor] = []
    for extractor in EXTRACTORS:
        try:
            if extractor.can_handle(data):
                claimants.append(extractor)
        except Exception:
            # A broken extractor must never abort the whole scan.
            pass

    if len(claimants) == 0:
        return ScanResult(
            claimants=[],
            extractor=None,
            extraction=None,
            destination=DEST_UNKNOWN,
            detail="no extractor matched",
        )

    if len(claimants) > 1:
        names = ", ".join(f"{e.__class__.__name__}({e.name})" for e in claimants)
        return ScanResult(
            claimants=claimants,
            extractor=None,
            extraction=None,
            destination=DEST_CONTESTED,
            detail=f"claimed by: {names}",
        )

    # Exactly one claimant — run full extraction to check match_key.
    extractor = claimants[0]
    try:
        extraction = extractor.extract(data, filename)
    except Exception as exc:
        return ScanResult(
            claimants=claimants,
            extractor=extractor,
            extraction=None,
            destination=DEST_SW_MISSING,
            detail=f"extraction error: {exc}",
        )

    sw = extraction.get("software_version")
    cal = extraction.get("calibration_id")
    family = extraction.get("ecu_family") or ""
    variant = extraction.get("ecu_variant") or ""
    hw = extraction.get("hardware_number") or ""
    key = extraction.get("match_key") or ""

    family_display = family or "?"
    variant_part = f"  variant: {variant}" if variant and variant != family else ""

    if key:
        if sw:
            version_detail = f"sw: {sw}"
        elif cal:
            version_detail = f"cal_id: {cal} (sw absent by architecture)"
        else:
            version_detail = f"sw: {sw}"

        detail = (
            f"extractor: {extractor.__class__.__name__}  "
            f"family: {family_display}"
            + variant_part
            + f"  {version_detail}"
            + (f"  hw: {hw}" if hw else "")
            + (f"  key: {key}" if key else "")
        )
        destination = DEST_SCANNED
    else:
        detail = (
            f"extractor: {extractor.__class__.__name__}  "
            f"family: {family_display}"
            + variant_part
            + f"  sw: None"
            + (f"  cal_id: {cal}" if cal else "")
            + (f"  hw: {hw}" if hw else "")
        )
        destination = DEST_SW_MISSING

    return ScanResult(
        claimants=claimants,
        extractor=extractor,
        extraction=extraction,
        destination=destination,
        detail=detail,
    )
