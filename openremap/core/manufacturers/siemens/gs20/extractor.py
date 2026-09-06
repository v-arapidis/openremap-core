"""
Siemens GS20 ECU binary extractor (BMW automatic-gearbox controller, AGS).

The GS20 family is a Siemens **automatic-gearbox controller (AGS)** for BMW —
not a VAG ECU.  Oracle ground truth (ms4x wiki + a community BMW flash-tool
corpus, dev-machine only):

  - GS20 ident key ``G2210_`` → variant ``GS20`` (community variant tables).
  - Live-ECU ident fields are all 7-digit numbers: ``ID_BMW_NR`` (HWNR,
    e.g. ``7544721``), ``AIF_ZB_NR`` (assembly/ZB), ``AIF_SW_NR`` (software).
  - The ``$REFERENZ`` data-section reference is ``G2210_0089D0`` (ident key
    ``G2210_``).

Binary characteristics — the 64 KB dump is the GS20 **program** region (the
full unit is 512 KB = 256 KB data + 64 KB program):

  - File size: exactly 64 KB (65 536 bytes / 0x10000).
  - CPU: 16-bit (not decoded — no capstone mapping yet).
  - Footer (tail of the file) carries the repeated ``$REFERENZ``
    ``G2210_0090C0AD10`` (first occurrence at 0xFFC8) — the oracle-backed
    GS20 identifier.
  - Header ident block near 0x170 carries ``GADC9010`` (at 0x176) and
    ``5430P523.`` (at 0x181).
  - A single 7-digit number ``7557997`` at 0x3FD8.

Field mapping (heuristic — no private A2L/DAMOS):

  - ``ecu_family = "GS20"`` — oracle-backed (the ``G2210_`` ident key).
  - ``software_version = "7557997"`` — the only numeric identifier in the
    dump.  AMBIGUOUS: the oracle shows GS20 ident numbers are all 7-digit
    (HW ``7544721`` / ZB / SW), so this value could be hardware, ZB or
    software.  It is kept as ``software_version`` so a stable match key
    (``GS20::7557997``) exists — do not read it as a verified SW number.
  - ``calibration_id = "GADC9010"`` — header code, no oracle reference;
    treated as the calibration identifier heuristically.
  - ``hardware_number = None`` — the oracle HWNR ``7544721`` is not stored
    as plain ASCII in these dumps, so no value is fabricated.  The full
    HWNR/ZB/SW fields likely live in the 256 KB "data" region we don't have.
"""

import hashlib
import re
from typing import Dict, List, Optional

from openremap.core.manufacturers.base import (
    FAMILY_STRING,
    IDENT_BLOCK,
    SIZE_MATCH,
    BaseManufacturerExtractor,
    DetectionStrength,
)

#: The GS20 family reports under the Siemens manufacturer.
FAMILY = "GS20"

#: Unique file size — exactly 64 KB.
VALID_FILE_SIZE = 0x10000

#: Header calibration-code signature, fixed at 0x176 (bounded search window).
HEADER_SIGNATURE = b"GADC9010"
HEADER_REGION = slice(0, 0x200)

#: Footer project/part-string signature, repeated in the file tail.
FOOTER_SIGNATURE = b"G2210_0090C0AD10"
FOOTER_REGION = slice(0xFFC0, 0x10000)

#: 7-digit numeric version identifier at 0x3FD8.
SW_OFFSET = 0x3FD8
SW_LENGTH = 7

#: Calibration code at 0x176.
CAL_OFFSET = 0x176
CAL_LENGTH = 8

#: Declared ident block (header region) for the confidence scorer's
#: ident-block cross-check — shorter than the generic 64-byte heuristic.
IDENT_BLOCK_REGION = (0x170, 0x1A0)

_DIGITS_RE = re.compile(rb"[0-9]{7}")
_CAL_RE = re.compile(rb"[A-Za-z0-9]{6,8}")


class SiemensGS20Extractor(BaseManufacturerExtractor):
    """
    Extractor for the Siemens GS20 automatic-gearbox controller (AGS).

    Detection is anchored on:
      - File size gate (exactly 64 KB)
      - Header calibration code ``GADC9010`` in the first 512 bytes
      - Footer project/part string ``G2210_0090C0AD10`` in the file tail
    """

    detection_strength = DetectionStrength.STRONG

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Siemens"

    @property
    def supported_families(self) -> List[str]:
        return [FAMILY]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        if len(data) != VALID_FILE_SIZE:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        if HEADER_SIGNATURE not in data[HEADER_REGION]:
            self._set_evidence()
            return False
        evidence.append(IDENT_BLOCK)

        if FOOTER_SIGNATURE not in data[FOOTER_REGION]:
            self._set_evidence()
            return False
        evidence.append(FAMILY_STRING)

        self._set_evidence(evidence)
        return True

    # -----------------------------------------------------------------------
    # Extraction
    # -----------------------------------------------------------------------

    def extract(self, data: bytes, filename: str = "unknown.bin") -> Dict:
        result: Dict = {
            "manufacturer": self.name,
            "file_size": len(data),
            "md5": hashlib.md5(data).hexdigest(),
            "sha256_first_64kb": hashlib.sha256(data[:0x10000]).hexdigest(),
        }

        result["raw_strings"] = self.extract_raw_strings(
            data=data,
            region=slice(0x160, 0x1C0),
            min_length=6,
            max_results=20,
        )

        software_version = self._read_software_version(data)
        calibration_id = self._read_calibration_id(data)

        result["software_version"] = software_version
        result["calibration_id"] = calibration_id
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["hardware_number"] = None
        result["oem_part_number"] = None
        result["serial_number"] = None
        result["dataset_number"] = None
        result["ecu_variant"] = None
        result["ecu_family"] = FAMILY
        result["ident_block"] = IDENT_BLOCK_REGION

        result["match_key"] = self.build_match_key(
            ecu_family=FAMILY,
            ecu_variant=None,
            software_version=software_version,
        )

        return result

    # -----------------------------------------------------------------------
    # Internal — field readers
    # -----------------------------------------------------------------------

    def _read_software_version(self, data: bytes) -> Optional[str]:
        """Read the 7-digit numeric version identifier at 0x3FD8."""
        field = data[SW_OFFSET : SW_OFFSET + SW_LENGTH]
        if _DIGITS_RE.fullmatch(field):
            return field.decode("ascii")
        return None

    def _read_calibration_id(self, data: bytes) -> Optional[str]:
        """Read the 8-character calibration code at 0x176."""
        field = data[CAL_OFFSET : CAL_OFFSET + CAL_LENGTH]
        if _CAL_RE.fullmatch(field):
            return field.decode("ascii")
        return None
