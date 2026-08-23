"""
Denso SH7055 ECU binary extractor (16-bit Subaru application).

Implements BaseManufacturerExtractor for the Denso SH7055-based ECU used
in 2001-2004 Subaru models (Impreza/WRX/STi, Forester, Liberty/Legacy).

Binary layout:

  160 KB (0x28000) or 192 KB (0x30000)

  The identity block sits at the top of the file:

    0x01E0   0x02 0x40 pairs (SH7055 vector-table tail)
    0x01FE   0x02 0x40 marker immediately before the CAL ID
    0x0200   CAL ID, 8 ASCII chars (e.g. "A4RG060P")
    0x0208   0x00
    0x0209   "CACopyrightDENSO2002" anchor string

  Unlike the 32-bit Denso layouts, the 16-bit block has no 8-character
  internal ID field and no descriptor bytes.
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
    DENSO_COPYRIGHT,
    DensoFamily,
    find_cal_id,
)

VALID_FILE_SIZES = (0x28000, 0x30000)

# 0x02 0x40 pair immediately before the CAL ID field.
CAL_MARKER = b"\x02\x40"

CAL_OFFSET = 0x200

# Copyright anchor sits at CAL_OFFSET + 9 and is ~22 bytes long.
COPYRIGHT_REGION = slice(0x209, 0x230)


class DensoSH7055Extractor(BaseManufacturerExtractor):
    """
    Extractor for the Denso SH7055 16-bit ECU (Subaru application).

    Detection is anchored on:
      - File size gate (160 KB or 192 KB)
      - 0x02 0x40 marker bytes immediately before the CAL ID field
      - CAL ID shape at the fixed 0x200 offset
      - "CopyrightDENSO" anchor string after the CAL ID
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
        return [DensoFamily.SH7055]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        if len(data) not in VALID_FILE_SIZES:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        if data[0x1FE:0x200] != CAL_MARKER:
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)

        if find_cal_id(data, CAL_OFFSET) is None:
            self._set_evidence()
            return False
        evidence.append(IDENT_BLOCK)

        if DENSO_COPYRIGHT not in data[COPYRIGHT_REGION]:
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
            region=slice(0x1E0, 0x400),
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
        result["ident_block"] = (0x1E0, 0x230)

        ecu_family = DensoFamily.SH7055
        ecu_variant: Optional[str] = None
        result["ecu_family"] = ecu_family
        result["ecu_variant"] = ecu_variant

        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            ecu_variant=ecu_variant,
            software_version=cal_id,
        )

        return result
