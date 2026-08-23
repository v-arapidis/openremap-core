"""
Hitachi SH72546 ECU binary extractor (Subaru application).

Implements BaseManufacturerExtractor for the Hitachi SH72546-based ECU
used in 2013+ Subaru models (Forester SJG DIT, WRX S4).

Binary layout:

  1.5 MB (0x180000) or 2 MB (0x200000)

  Unlike the Denso Subaru units, there is no fixed identity block at a
  known offset and no Denso copyright string.  Instead the CAL ID is
  stored as plain ASCII immediately after a b"T\\x00" tag:

    0x18FC2 (1.5 MB)   b"T\\x00" tag
    0x18FC4             CAL ID, 8 ASCII chars (e.g. "AF5G200A")
    0x18FCC             engine descriptor text (e.g. "2.0 TURBO")

    0x2AA1E (2 MB)      b"T\\x00" tag
    0x2AA20             CAL ID, 8 ASCII chars (e.g. "LV9N100B")
    0x2AA2C             engine descriptor text (e.g. "2.0 TURBO")

  The engine descriptor text ("2.0 TURBO", "2.5 ", "3.6 ", ...) follows
  the CAL ID immediately or after a few NUL bytes and distinguishes the
  real CAL ID from other b"T\\x00"-tagged strings in the file (e.g.
  "C0C0C0CQD" block labels).
"""

import hashlib
import re
from typing import Dict, List, Optional

from openremap.core.manufacturers.base import (
    IDENT_BLOCK,
    MAGIC_MATCH,
    SIZE_MATCH,
    BaseManufacturerExtractor,
    DetectionStrength,
)

VALID_FILE_SIZES = (0x180000, 0x200000)

#: The b"T\x00" tag preceding the CAL ID.
CAL_TAG = b"T\x00"

#: Engine descriptor text expected within this many bytes after the CAL ID.
ENGINE_TEXT_WINDOW = 16

#: Engine descriptor hints (e.g. "2.0 TURBO", "3.6R ").
ENGINE_TEXT_HINTS = (b"TURBO", b"DIT")

_ENGINE_DISPLACEMENT = re.compile(rb"(2\.[05]|3\.[06])")

CAL_ID_PATTERN = re.compile(rb"[A-Z]{1,2}[0-9][A-Za-z0-9]{3,6}")


class HitachiSH72546Extractor(BaseManufacturerExtractor):
    """
    Extractor for the Hitachi SH72546 ECU (Subaru application).

    Detection is anchored on:
      - File size gate (1.5 MB or 2 MB)
      - b"T\\x00" tag immediately before a CAL-ID-shaped 8-char field
      - Engine descriptor text right after the CAL ID

    The b"T\\x00"-tagged CAL ID search is a bounded byte-scan, still
    cheap because it only walks the occurrences of a 2-byte tag.
    """

    detection_strength = DetectionStrength.MODERATE

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Hitachi"

    @property
    def supported_families(self) -> List[str]:
        return ["SH72546"]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def _find_cal_id(self, data: bytes) -> Optional[int]:
        """Return the CAL ID offset, or None."""
        search_from = 0
        while True:
            tag = data.find(CAL_TAG, search_from)
            if tag < 0:
                return None
            cal_off = tag + len(CAL_TAG)
            field = data[cal_off : cal_off + 8]
            if CAL_ID_PATTERN.fullmatch(field):
                after = data[cal_off + 8 : cal_off + 8 + ENGINE_TEXT_WINDOW]
                if any(hint in after for hint in ENGINE_TEXT_HINTS) or _ENGINE_DISPLACEMENT.search(after):
                    return cal_off
            search_from = tag + 1

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        if len(data) not in VALID_FILE_SIZES:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        cal_off = self._find_cal_id(data)
        if cal_off is None:
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)
        evidence.append(IDENT_BLOCK)

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

        cal_off = self._find_cal_id(data)
        if cal_off is not None:
            region_start = max(0, cal_off - 0x40)
            result["raw_strings"] = self.extract_raw_strings(
                data=data,
                region=slice(region_start, cal_off + 0x40),
                min_length=6,
                max_results=20,
            )
            cal_id = data[cal_off : cal_off + 8].decode("ascii")
        else:
            result["raw_strings"] = []
            cal_id = None

        result["software_version"] = cal_id
        result["calibration_id"] = None
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["hardware_number"] = None
        result["oem_part_number"] = None
        result["serial_number"] = None
        result["dataset_number"] = None
        result["ident_block"] = (
            (cal_off - 0x40, cal_off + 0x60) if cal_off is not None else None
        )

        ecu_family = "SH72546"
        ecu_variant: Optional[str] = None
        result["ecu_family"] = ecu_family
        result["ecu_variant"] = ecu_variant

        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            ecu_variant=ecu_variant,
            software_version=cal_id,
        )

        return result
