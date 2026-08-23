"""
Denso EE20 diesel ECU binary extractor (Subaru application).

Implements BaseManufacturerExtractor for the Denso diesel ECU used in
2009-2012 Subaru EE20 boxer-diesel models (Forester/Impreza 2.0D).

Binary layout:

  1 MB (0x100000)

  Unlike the petrol SH72531 units, the diesel identity block sits at
  0x4000 and its copyright anchor is spelled "Cpyr.DENSO" (missing "o"):

    0x4000   b"K" + 3 digits (production marker, e.g. "K321")
    0x4004   b"ZQ2DT" (block tag)
    0x4009   3 digits
    0x400C   CAL ID, 8 ASCII chars (e.g. "JZ2F401A")
    0x4014   spaces
    0x4023   "Cpyr.DENSO2009" anchor string

  A boot loader string "IBL_xx" is stored near 0x1060.
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
    DENSO_CPYR,
    DensoFamily,
    find_cal_id,
)

VALID_FILE_SIZES = (0x100000,)

#: Identity block base offset.
BLOCK_OFFSET = 0x4000

#: "K" + 3 digits + "ZQ2DT" + 3 digits precedes the CAL ID (12 bytes).
BLOCK_PREFIX_LENGTH = 12

#: CAL ID offset.
CAL_OFFSET = BLOCK_OFFSET + BLOCK_PREFIX_LENGTH

#: "Cpyr.DENSO" anchor search window.
DENSO_REGION = slice(0x4000, 0x4060)


class DensoDieselExtractor(BaseManufacturerExtractor):
    """
    Extractor for the Denso EE20 diesel ECU (Subaru application).

    Detection is anchored on:
      - File size gate (1 MB)
      - b"K" + 3 digits + b"ZQ2DT" block tag at 0x4000
      - CAL ID shape at the fixed 0x400C offset
      - "Cpyr.DENSO" anchor string in the identity block

    Runs before DensoSH72531Extractor (same 1 MB size) because its
    identity block and anchor string are distinct from every petrol
    layout.
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
        return [DensoFamily.DIESEL]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        if len(data) not in VALID_FILE_SIZES:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        block = data[BLOCK_OFFSET : BLOCK_OFFSET + 4]
        if not (
            block[0] == 0x4B  # "K"
            and 0x30 <= block[1] <= 0x39
            and 0x30 <= block[2] <= 0x39
            and 0x30 <= block[3] <= 0x39
        ):
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)

        if data[BLOCK_OFFSET + 4 : BLOCK_OFFSET + 9] != b"ZQ2DT":
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)

        if find_cal_id(data, CAL_OFFSET) is None:
            self._set_evidence()
            return False
        evidence.append(IDENT_BLOCK)

        if DENSO_CPYR not in data[DENSO_REGION]:
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
            region=slice(0x3FF0, 0x4100),
            min_length=6,
            max_results=20,
        )

        cal_id = find_cal_id(data, CAL_OFFSET)
        result["software_version"] = cal_id
        result["calibration_id"] = None
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["hardware_number"] = None
        result["oem_part_number"] = None
        result["serial_number"] = None
        result["dataset_number"] = None
        result["ident_block"] = (0x3FF0, 0x4060)

        ecu_family = DensoFamily.DIESEL
        ecu_variant: Optional[str] = None
        result["ecu_family"] = ecu_family
        result["ecu_variant"] = ecu_variant

        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            ecu_variant=ecu_variant,
            software_version=cal_id,
        )

        return result
