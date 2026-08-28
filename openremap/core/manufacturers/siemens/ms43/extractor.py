"""
Siemens MS43 ECU binary extractor.

Implements BaseManufacturerExtractor for the Siemens MS43 petrol ECU family,
used in BMW E46 / M54 (and E85 / M54) vehicles, ~2000–2006.  The MS43 is a
C167-based controller; this extractor only reads identity metadata — no
decoder involvement (the C166 xref signal is driven separately through
``arch_for_family("Siemens", "MS43")`` → ("c166", 0, 0, False)).

Binary characteristics (verified byte-for-byte on the real 4-file corpus):
  - File size: exactly 512 KB (524288 bytes)
  - Hardware part numbers use the Siemens 5WK9 prefix
    ("5WK90027" — NOTE: 5WK9 is shared with Simtec56 (128 KB); the size
    gate is what disambiguates)
  - Literal "MS43" family string in the ident record at 0x3F94 (unique)
  - Program number (the community software identifier) from the "ca<num>.DAT"
    calibration dataset reference at 0x70040, or the tail ident block at
    0x6FFBA; match key "MS43::430069"
  - Ident record at 0x3F80: "5WK90027--1061330037MS43..."

The analyzer.py is manufacturer-agnostic — it discovers this extractor
via the registry in manufacturers/__init__.py.
"""

import hashlib
import re
from typing import Dict, List, Optional

from openremap.core.manufacturers.base import (
    DETECTION_SIGNATURE,
    EXCLUSION_CLEAR,
    FAMILY_ANCHOR,
    SIZE_MATCH,
    BaseManufacturerExtractor,
    DetectionStrength,
)
from openremap.core.manufacturers.siemens.ms43.patterns import (
    DETECTION_SIGNATURES,
    EXCLUSION_SIGNATURES,
    IDENT_BLOCK,
    MS43_FILE_SIZE,
    PATTERN_REGIONS,
    PATTERNS,
    PROGRAM_NUMBER_OFFSET,
    SEARCH_REGIONS,
)


class SiemensMS43Extractor(BaseManufacturerExtractor):
    """
    Extractor for Siemens MS43 petrol ECU binaries.

    Handles: MS43

    These ECUs are found in BMW E46/M54 petrol vehicles from approximately
    2000–2006.  The binaries are always exactly 512 KB (524288 bytes) and
    contain a structured ident record at 0x3F80 with the Siemens 5WK9
    hardware part number, a 10-digit production serial, and the literal
    "MS43" family string at 0x3F94.

    Detection relies on:
      1. Exact file size (524288 bytes) — mandatory gate; also separates
         MS43 from Simtec56 (both use the 5WK9 prefix, sizes differ)
      2. The literal "MS43" family string in the first 128 KB (primary,
         unique) plus at least one secondary signature: 5WK9 hardware
         prefix or the ca43...DAT calibration dataset reference
      3. Absence of Bosch / other-Siemens exclusion signatures
    """

    detection_strength = DetectionStrength.STRONG

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable manufacturer name."""
        return "Siemens"

    @property
    def supported_families(self) -> List[str]:
        """ECU families handled by this extractor."""
        return ["MS43"]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        """
        Return True if this binary belongs to a Siemens MS43 ECU.

        Detection strategy (all conditions must be met):
          1. File size must be exactly 524288 bytes (512 KB).
             Every known MS43 dump is this exact size — no exceptions.
             This is also what disambiguates MS43 from Simtec56, which
             shares the 5WK9 hardware prefix at 128 KB.

          2. No exclusion signature may be present (searched across the
             full binary): Bosch families (EDC17/MED17/ME7./ME9) and the
             other Siemens families (SID801/SID803/PPD/SIMOS/5WS4).

          3. Positive detection:
             - b"MS43"  — family literal, PRIMARY (unique to MS43).  Must
               appear in the first 128 KB (it lives at 0x3F94).
             - AND at least one of:
                 * b"5WK9"  — hardware prefix, SECONDARY (shared with
                   Simtec56; the size gate handles that case).  Scanned
                   in the first 128 KB (it lives at 0x3F80).
                 * ca43...DAT calibration dataset reference — the file
                   reference lives at 0x70040, PAST the 128 KB scan
                   window, so it is searched across the full binary.

        Args:
            data: Raw bytes of the ECU binary file

        Returns:
            True if this extractor should handle the binary
        """
        evidence: list[str] = []

        # ------------------------------------------------------------------
        # Gate 1 — exact file size
        # ------------------------------------------------------------------
        if len(data) != MS43_FILE_SIZE:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        # ------------------------------------------------------------------
        # Gate 2 — exclusion signatures (reject Bosch / other Siemens bins)
        # ------------------------------------------------------------------
        for sig in EXCLUSION_SIGNATURES:
            if sig in data:
                self._set_evidence()
                return False
        evidence.append(EXCLUSION_CLEAR)

        # ------------------------------------------------------------------
        # Gate 3 — positive detection
        # ------------------------------------------------------------------
        # MS43 (primary, unique) must be present in the first 128 KB, plus
        # at least one secondary signature.  Scan only the first 128 KB for
        # the byte signatures (all live in the first 64 KB); the ca43...DAT
        # calibration reference sits at 0x70040 so it is searched across
        # the full binary.
        detection_region = data[:0x20000]
        has_ms43 = b"MS43" in detection_region
        has_5wk9 = b"5WK9" in detection_region
        has_cal = re.search(PATTERNS["calibration_dataset"], data) is not None
        if has_ms43 and (has_5wk9 or has_cal):
            evidence.append(FAMILY_ANCHOR)
            evidence.append(DETECTION_SIGNATURE)
            self._set_evidence(evidence)
            return True

        self._set_evidence()
        return False

    # -----------------------------------------------------------------------
    # Extraction
    # -----------------------------------------------------------------------

    def extract(self, data: bytes, filename: str = "unknown.bin") -> Dict:
        """
        Extract all identifying information from a Siemens MS43 binary.

        Returns a dict fully compatible with ECUIdentifiersSchema.

        Extraction pipeline:
          1. Compute mandatory hash fields (md5, sha256_first_64kb)
          2. Extract raw ASCII strings from header (display + fallback)
          3. Run all regex patterns against their assigned search regions
          4. Resolve each field via dedicated resolver methods
          5. Build the compound match key
          6. Return the complete identification dict

        Args:
            data:     Raw bytes of the ECU binary file
            filename: Original filename — used for display only

        Returns:
            Dict compatible with ECUIdentifiersSchema
        """
        result: Dict = {
            "manufacturer": self.name,
            "file_size": len(data),
            "md5": hashlib.md5(data).hexdigest(),
            "sha256_first_64kb": hashlib.sha256(data[:0x10000]).hexdigest(),
        }

        # --- Step 1: Raw ASCII strings from the ident area (display + fallback) ---
        # The MS43 ident record lives at 0x3F80 — past the 4 KB header — so
        # the 64 KB ident_area is the right scan window for raw strings.
        result["raw_strings"] = self.extract_raw_strings(
            data=data,
            region=SEARCH_REGIONS["ident_area"],
            min_length=8,
            max_results=20,
        )

        # --- Step 2: Run all patterns against their assigned regions ---
        raw_hits = self._run_all_patterns(
            data, PATTERNS, PATTERN_REGIONS, SEARCH_REGIONS
        )

        # --- Step 3: Resolve hardware number ---
        # The 5WK9xxxx part number identifies the ECU hardware revision
        # (e.g. "5WK90027"), from the ident record at 0x3F80.
        hardware_number = self._resolve_hardware_number(raw_hits)
        result["hardware_number"] = hardware_number

        # --- Step 4: Resolve software version ---
        # The program number (community software identifier, e.g. "430069")
        # is the primary matching key — from the "ca<num>.DAT" calibration
        # dataset reference first, then the tail ident block offset.
        software_version = self._resolve_software_version(raw_hits, data)
        result["software_version"] = software_version

        # --- Step 5: Resolve ECU family ---
        # Literal "MS43" from the ident record, constant default otherwise.
        ecu_family = self._resolve_ecu_family(raw_hits)
        result["ecu_family"] = ecu_family

        # --- Step 6: Resolve calibration ID ---
        # The calibration dataset filename reference, e.g. "ca430069.DAT".
        result["calibration_id"] = self._resolve_calibration_id(raw_hits)

        # --- Step 7: Resolve serial number ---
        # 10-digit production serial from the ident record, e.g. "1061330037".
        result["serial_number"] = self._first_hit(raw_hits, "serial_number")

        # --- Step 8: Resolve OEM part number ---
        # 7-digit BMW/OEM part number.  Verified ABSENT on the real corpus
        # (stored as consecutive repeated runs), so None on real files.
        result["oem_part_number"] = self._first_hit(raw_hits, "oem_part_number")

        # --- Step 9: Fields not applicable to MS43 ---
        result["ecu_variant"] = None
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["dataset_number"] = None

        # --- Step 10: Emit the declared ident block ---
        # The ident record at 0x3F80 is a short printable block; declaring
        # it lets the confidence scorer's ident-block cross-check honour it
        # instead of the generic 64-byte printable-run heuristic.
        result["ident_block"] = IDENT_BLOCK

        # --- Step 11: Build compound match key ---
        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            software_version=software_version,
        )

        return result

    # -----------------------------------------------------------------------
    # Internal — field resolvers
    # -----------------------------------------------------------------------

    def _resolve_hardware_number(self, raw_hits: Dict[str, List[str]]) -> Optional[str]:
        """
        Resolve the Siemens hardware part number (e.g. "5WK90027").

        The 5WK9 prefix is shared with the Simtec56 family; the size gate
        in can_handle() guarantees this binary is MS43 (524288 bytes).

        Returns:
            The first matched 5WK9 part number string, or None.
        """
        return self._first_hit(raw_hits, "siemens_part")

    def _resolve_software_version(
        self, raw_hits: Dict[str, List[str]], data: Optional[bytes] = None
    ) -> Optional[str]:
        """
        Resolve the software version — the MS43 program number.

        The program number (e.g. "430069") is the community-standard
        software identifier — the thing in filenames like
        "..._430069_..." and in the calibration dataset filename.

        Extraction strategy (priority order):
          1. Capture group of the calibration dataset reference "ca<num>.DAT"
             (the reference at 0x70040 is "ca430069.DAT" → "430069").
             Self-describing and present in every MS43 binary.
          2. Fixed offset 0x6FFBA in the tail ident block — 6 ASCII digits.
          3. Standalone bounded "43xxxx" digit-run pattern (documented
             fallback for hypothetical binaries without a ca...DAT ref).

        Do NOT use the SW-ID area ("000000115852" @ 0x3C34) or the
        SW-number tail ("96577355117" @ 0x6FFBF) — those are checksum init
        sources, not software identifiers.

        Returns:
            The program number string, or None.
        """
        # Priority 1 — program number from the calibration dataset reference
        cal = self._first_hit(raw_hits, "calibration_dataset")
        if cal:
            m = re.search(rb"ca(\d+)\.DAT", cal.encode("ascii", errors="ignore"))
            if m:
                return m.group(1).decode("ascii")

        # Priority 2 — fixed offset in the tail ident block (6 ASCII digits)
        if data is not None and len(data) >= PROGRAM_NUMBER_OFFSET + 6:
            chunk = data[PROGRAM_NUMBER_OFFSET : PROGRAM_NUMBER_OFFSET + 6]
            if chunk.isdigit():
                return chunk.decode("ascii")

        # Priority 3 — standalone program-number pattern (documented fallback)
        return self._first_hit(raw_hits, "program_number")

    def _resolve_ecu_family(self, raw_hits: Dict[str, List[str]]) -> str:
        """
        Resolve the ECU family — always "MS43".

        The literal "MS43" family string lives in the ident record at
        0x3F94.  If the pattern missed it, can_handle() already confirmed
        the MS43 anchor, so returning the constant default is safe.

        Returns:
            "MS43".
        """
        family_hit = self._first_hit(raw_hits, "family")
        if family_hit:
            normalised = family_hit.upper().strip()
            if normalised.startswith("MS43"):
                return "MS43"
        return "MS43"

    def _resolve_calibration_id(self, raw_hits: Dict[str, List[str]]) -> Optional[str]:
        """
        Resolve the calibration identifier — the dataset filename reference.

        e.g. "ca430069.DAT" (at 0x70040).  This is the calibration dataset
        file used during production; its capture group is also the program
        number used for software_version.

        Returns:
            The calibration dataset filename string, or None.
        """
        return self._first_hit(raw_hits, "calibration_dataset")

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"manufacturer={self.name!r} "
            f"families={self.supported_families}>"
        )
