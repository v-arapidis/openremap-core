"""
Denso SH72531 ECU binary extractor (32-bit CAN Subaru application).

Implements BaseManufacturerExtractor for the Denso SH72531-based ECU used
in 2006-2014 Subaru models (Impreza/WRX/STi, Forester, Liberty/Legacy,
Outback, Tribeca, XV).

Binary layout:

  1 MB (0x100000) nominal, plus rare dumps shifted by a few bytes
  (0x10009B, 0x1000D0).  The identity block sits at offset 0x1FFC plus
  the same shift ("delta" = file size - 0x100000).

    0x1FFC+delta   4-byte descriptor: byte 0 in {0x33, 0x34},
                   byte 1 in {0x41, 0x61, 0xA1, 0xA2},
                   bytes 2-3 in {b"\\x00\\x01", b"\\x00\\x03", b"\\x00\\x04"}

  Two CAL ID layouts exist, selected by descriptor byte 1:

    A1 layout (0x41 / 0x61 / 0xA1) — CAL ID directly at 0x2000+delta:

        0x2000+delta   CAL ID, 8 ASCII chars (e.g. "A8DH100E")
        0x2008+delta   0x00
        0x2009+delta   Internal ID, 8 ASCII chars (e.g. "85DK4_A")

    A2 layout (0xA2) — 4 prefix bytes, then CAL ID at 0x2004+delta:

        0x2000+delta   b"\\xff\\xff\\x20\\x4c" (" L" prefix) or
                       b"\\xff\\xff\\xbf\\x7c" (two 2008 PZEV ROMs)
        0x2004+delta   CAL ID, 8 ASCII chars (e.g. "AZ1J500T")
        0x200C+delta   0x00
        0x200D+delta   Internal ID, 8 ASCII chars (e.g. "PD5H4T K3")

  Both layouts carry "Copr.DENSO20XX" ~0x23 bytes after the CAL ID.
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

#: Nominal file size; everything above it up to +0xFF is a shifted dump.
NOMINAL_SIZE = 0x100000

#: Descriptor byte 0 values.
MARKER_BYTE0 = (0x33, 0x34)

#: Descriptor byte 1 values (A1: 0x41/0x61/0xA1, A2: 0xA2).
MARKER_BYTE1 = (0x41, 0x61, 0xA1, 0xA2)

#: Descriptor bytes 2-3 values.
MARKER_TAIL = (b"\x00\x01", b"\x00\x03", b"\x00\x04")

#: A2-layout CAL ID prefixes.
A2_PREFIXES = (b"\xff\xff\x20\x4c", b"\xff\xff\xbf\x7c")

#: CAL ID offset for the A1 layout (relative to the identity block).
CAL_OFFSET_A1 = 0x2000

#: CAL ID offset for the A2 layout (relative to the identity block).
CAL_OFFSET_A2 = 0x2004

#: Descriptor offset relative to the identity-block base.
MARKER_OFFSET = 0x1FFC

#: "Copr.DENSO" search window (relative to the identity-block base).
DENSO_REGION = slice(0x2010, 0x2040)


def _identity_base(size: int) -> Optional[int]:
    """
    Resolve the identity-block base offset for a candidate file size.

    Returns None for sizes that cannot be a Denso SH72531 dump.
    """
    if size == NOMINAL_SIZE:
        return 0
    if NOMINAL_SIZE < size <= NOMINAL_SIZE + 0xFF:
        return size - NOMINAL_SIZE
    return None


class DensoSH72531Extractor(BaseManufacturerExtractor):
    """
    Extractor for the Denso SH72531 32-bit CAN ECU (Subaru application).

    Detection is anchored on:
      - File size gate (1 MB, or 1 MB plus a small shift)
      - 4-byte descriptor at 0x1FFC+delta
      - CAL ID shape at 0x2000+delta (A1) or 0x2004+delta (A2)
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
        return [DensoFamily.SH72531]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        evidence: list[str] = []

        base = _identity_base(len(data))
        if base is None:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        marker = data[base + MARKER_OFFSET : base + MARKER_OFFSET + 4]
        if (
            marker[0] not in MARKER_BYTE0
            or marker[1] not in MARKER_BYTE1
            or marker[2:4] not in MARKER_TAIL
        ):
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)

        if marker[1] == 0xA2:
            prefix = data[base + CAL_OFFSET_A1 : base + CAL_OFFSET_A1 + 4]
            if prefix not in A2_PREFIXES:
                self._set_evidence()
                return False
            cal_off = base + CAL_OFFSET_A2
        else:
            cal_off = base + CAL_OFFSET_A1

        if find_cal_id(data, cal_off) is None:
            self._set_evidence()
            return False
        evidence.append(IDENT_BLOCK)

        region_start = base + DENSO_REGION.start
        region_stop = base + DENSO_REGION.stop
        if b"DENSO" not in data[region_start:region_stop]:
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

        base = _identity_base(len(data)) or 0
        marker = data[base + MARKER_OFFSET + 1]
        cal_off = (
            base + CAL_OFFSET_A2
            if marker == 0xA2
            else base + CAL_OFFSET_A1
        )

        result["raw_strings"] = self.extract_raw_strings(
            data=data,
            region=slice(cal_off - 0x14, cal_off + 0x60),
            min_length=6,
            max_results=20,
        )

        cal_id = find_cal_id(data, cal_off)
        result["software_version"] = cal_id
        result["calibration_id"] = find_internal_id(data, cal_off + 9)
        result["calibration_version"] = None
        result["sw_base_version"] = None
        result["hardware_number"] = None
        result["oem_part_number"] = None
        result["serial_number"] = None
        result["dataset_number"] = None
        result["ident_block"] = (cal_off - 0x14, cal_off + 0x4C)

        ecu_family = DensoFamily.SH72531
        ecu_variant: Optional[str] = None
        result["ecu_family"] = ecu_family
        result["ecu_variant"] = ecu_variant

        result["match_key"] = self.build_match_key(
            ecu_family=ecu_family,
            ecu_variant=ecu_variant,
            software_version=cal_id,
        )

        return result
