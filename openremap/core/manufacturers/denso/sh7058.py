"""
Denso SH7058 ECU binary extractor (32-bit Subaru application).

Implements BaseManufacturerExtractor for the Denso SH7058-based ECU used
in 2004-2007 Subaru models (Liberty/Legacy GT, Forester XT, Impreza).

Binary layout:

  512 KB (0x80000)

  Identity block:

    0x1FFC   4-byte descriptor (b"\\x31\\x91\\x00\\x05" or
              b"\\x34\\x11\\x00\\x01")
    0x2000   CAL ID, 8 ASCII chars (e.g. "A2WC400H")
    0x2008   0x00
    0x2009   Internal ID, 8 ASCII chars (e.g. "86CAU_AT")
    0x2023   "Copr.DENSO2005" anchor string
"""

import hashlib
from typing import Dict, List, Optional

from openremap.core.manufacturers.base import (
    FAMILY_STRING,
    IDENT_BLOCK,
    MAGIC_MATCH,
    SIZE_MATCH,
    BaseManufacturerExtractor,
    DetectionStrength,
)
from openremap.core.manufacturers.denso.base import (
    DensoFamily,
    find_cal_id,
    find_internal_id,
)

VALID_FILE_SIZES = (0x80000,)

# 4-byte descriptor immediately before the CAL ID field.
VALID_MARKERS = (b"\x31\x91\x00\x05", b"\x34\x11\x00\x01")

CAL_OFFSET = 0x2000

# "Copr.DENSO" anchor sits 0x23 bytes after the CAL ID.
DENSO_REGION = slice(0x2010, 0x2040)


class DensoSH7058Extractor(BaseManufacturerExtractor):
    """
    Extractor for the Denso SH7058 32-bit ECU (Subaru application).

    Detection is anchored on:
      - File size gate (512 KB)
      - Descriptor bytes at 0x1FFC
      - CAL ID shape at the fixed 0x2000 offset
      - "Copr.DENSO" anchor string in the identity block
    """

    detection_strength = DetectionStrength.STRONG

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Denso"

    @property
    def supported_families(self) -> List[str]:
        return [DensoFamily.SH7058]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        if len(data) not in VALID_FILE_SIZES:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        if data[0x1FFC:0x2000] not in VALID_MARKERS:
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)

        if find_cal_id(data, CAL_OFFSET) is None:
            self._set_evidence()
            return False
        evidence.append(IDENT_BLOCK)

        if b"DENSO" not in data[DENSO_REGION]:
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
            region=slice(0x1FF0, 0x2100),
            min_length=6,
            max_results=20,
        )

        cal_id = find_cal_id(data, CAL_OFFSET)
        result["software_version"] = cal_id
        result["calibration_id"] = find_internal_id(data, CAL_OFFSET + 9)
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["hardware_number"] = None
        result["oem_part_number"] = None
        result["serial_number"] = None
        result["dataset_number"] = None
        result["ident_block"] = (0x1FF0, 0x2040)

        ecu_family = DensoFamily.SH7058
        ecu_variant: Optional[str] = None
        result["ecu_family"] = ecu_family
        result["ecu_variant"] = ecu_variant

        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            ecu_variant=ecu_variant,
            software_version=cal_id,
        )

        return result
