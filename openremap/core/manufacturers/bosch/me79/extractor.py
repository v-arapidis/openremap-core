"""
Extractor for the Bosch ME7.9.x (ME7.9.10) ECU family.

ME7.9.10 (2006+, Fiat/Alfa/Lancia 1.4 T-Jet and PSA variants; the family is
also referenced as "ME7910" in flash markers and "ME7.9.1" in tuning
nomenclature) is a distinct ME7 sub-generation that the classic ME7
extractor deliberately rejects:

  - its ident block at 0x10000 starts with the ``ZZ<<`` dialect — the ME7
    can_handle() byte-3 guard requires an ASCII digit after ``ZZ`` and
    therefore skips these bins;
  - the calibration area carries a slash-delimited family marker like
    ``45/1/ME7910/9/F200M144//3207C/07C1F00/171007/``;
  - HW numbers are ``0261xxxxxx`` / ``0261Sxxxxx`` and SW numbers the usual
    10-digit ``1037xxxxxx``.

Real dumps in the corpus (832KB, Fiat Abarth 1.4 T-Jet): 851,968 bytes,
``ZZ<<ERCSU V1.1.6 SAB_C16x (c)ETAS Jul 24 1997`` at 0x10000.
"""

import hashlib
import re
from typing import Dict, List, Optional

from openremap.core.manufacturers.base import (
    FAMILY_STRING,
    HEADER_MATCH,
    MAGIC_MATCH,
    SIZE_MATCH,
    BaseManufacturerExtractor,
    DetectionStrength,
)
from openremap.core.manufacturers.bosch._vag import find_vag_part_number

#: The ME7.9.x boot/ident dialect at 0x10000 (vs. the "ZZ<digit>" format the
#: classic ME7 extractor requires).
_ZZ_DIALECT = b"ZZ<<"

#: Family marker found in the calibration area of real dumps.
_FAMILY_MARKERS = (b"ME7910", b"ME7.9")

#: 10-digit Bosch SW number ("1037392099").  The field is often followed by
#: zero padding in the dump ("1037392099000000"), so no trailing lookahead —
#: the real SW number is always the first 10 chars of the run.
_SW_RE = re.compile(rb"(?<![0-9])1037\d{6}")

#: Bosch HW number — "0261201685" or "0261S04662".  May sit inside a longer
#: alphanumeric field ("…70261S04662 "), so no leading guard beyond the
#: distinctive 0261 prefix; the trailing non-digit check keeps 0261 runs
#: inside longer numbers out.
_HW_RE = re.compile(rb"0261[S0-9]\d{5,6}(?![0-9])")


class BoschME79Extractor(BaseManufacturerExtractor):
    """
    Extractor for Bosch Motronic ME7.9.x (ME7.9.10) ECU binaries.
    """

    detection_strength = DetectionStrength.MODERATE

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "Bosch"

    @property
    def supported_families(self) -> List[str]:
        return ["ME7.9.10", "ME7910"]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        """
        Three-phase check:

          Phase 1 — size gate: real ME7.9.x dumps are >= 512KB (the corpus
                    samples are 832KB).  This keeps small legacy bins out.
          Phase 2 — the ``ZZ<<`` ident dialect at exactly 0x10000, followed
                    by printable ASCII (e.g. "ERCSU V1.1.6 ...").  The
                    classic ME7 extractor's byte-3 guard (ASCII digit) means
                    these bins are disjoint from ME7 claims.
          Phase 3 — family marker ("ME7910"/"ME7.9") anywhere in the dump.
        """
        evidence: List[str] = []

        # Phase 1 — size gate.
        if len(data) < 0x80000:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        # Phase 2 — ZZ<< dialect at 0x10000 with printable content after it.
        if data[0x10000:0x10004] != _ZZ_DIALECT:
            self._set_evidence()
            return False
        evidence.append(MAGIC_MATCH)
        if not all(32 <= b <= 126 for b in data[0x10004:0x10014]):
            self._set_evidence()
            return False
        evidence.append(HEADER_MATCH)

        # Phase 3 — family marker.
        if any(m in data for m in _FAMILY_MARKERS):
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
            "ecu_family": "ME7.9.10",
            "ecu_variant": None,
            "calibration_id": None,
            "calibration_version": None,
            "sw_base_version": None,
            "serial_number": None,
            "dataset_number": None,
        }

        m = _SW_RE.search(data)
        result["software_version"] = m.group(0).decode("ascii") if m else None

        m = _HW_RE.search(data)
        result["hardware_number"] = m.group(0).decode("ascii") if m else None

        result["oem_part_number"] = find_vag_part_number(data)

        # Raw ASCII strings around the ZZ<< ident block (first 64KB).
        result["raw_strings"] = self.extract_raw_strings(
            data=data,
            region=slice(0x10000, 0x10000 + 0x400),
            min_length=6,
            max_results=20,
        )

        result["match_key"] = self.build_match_key(
            ecu_family="ME7.9.10",
            ecu_variant=None,
            software_version=result["software_version"],
        )

        return result
