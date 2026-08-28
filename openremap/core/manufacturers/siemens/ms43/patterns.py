"""
Siemens MS43 ECU binary identifier patterns and search regions.

All regex patterns and search region definitions specific to Siemens MS43
petrol ECU binaries (BMW M54 / E46, C167-based, ~2000–2006).

Pattern reference (offsets verified byte-for-byte on the real 4-file corpus
in ``tests/data/ECUs/Siemens/MS43/`` — all 524288 bytes, one identity):

  IDENT RECORD        "5WK90027--1061330037MS43060414051158416577357604b-... "
    Full identification record at 0x3F80: Siemens part ``5WK90027``, serial
    ``1061330037``, family literal ``MS43`` at 0x3F94.
    THIS IS THE PRIMARY SOURCE for hardware_number and serial_number.

  HARDWARE NUMBER     "5WK90027"
    Siemens part number for the ECU hardware unit.
    Format: 5WK9 + 4–6 digits.  The ``5WK9`` prefix is shared with the
    Simtec56 family (128 KB) — the 524288-byte size gate disambiguates.

  FAMILY              "MS43"  (literal at 0x3F94)
    Controller family identifier.  Unique to this extractor.

  PROGRAM NUMBER      "430069"
    Community software identifier (the thing in filenames like
    "..._430069_..." and in the calibration dataset filename).
    Sources in priority order:
      1. Capture group of the calibration dataset reference "ca<num>.DAT"
         (the file at 0x70040 is "ca430069.DAT" → "430069").
      2. Fixed offset 0x6FFBA in the tail ident block (6 ASCII digits).
      3. Standalone bounded "43xxxx" digit run (fallback).
    THIS IS THE PRIMARY MATCHING KEY (match_key = "MS43::430069").

  CALIBRATION DATASET "ca430069.DAT"
    Calibration dataset file reference at 0x70040.

  SERIAL NUMBER       "1061330037"
    10-digit production serial inside the ident record (after "--").

  SW-ID AREA          "000000115852"  (12 ASCII digits at 0x3C34)
    Boot checksum init source (BE16 @ 0x3C34 = "00" -> 0x3030, documented
    in services/checksums/ms43.py).  Kept as a documented fallback source,
    NOT used as the software version.

  SW-NUMBER TAIL      "96577355117"  (11 ASCII digits at 0x6FFBF)
    Calibration checksum init source (BE16 @ 0x6FFBF).  Also a documented
    fallback source, NOT used as the software version.

  OEM PART NUMBER     "7551615"  (7 digits at 0x6FE80)
    BMW/OEM part number.  NOTE: verified as NOT cleanly extractable with a
    standalone-7-digit pattern — the corpus stores it as consecutive
    repeated runs ("7551615755161575...") so "(?<!\\d)\\d{7}(?!\\d)" never
    matches.  The pattern is kept for future corpus variation; the real
    corpus resolves oem_part_number to None.
"""

from typing import Dict

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
# All patterns are raw bytes (rb"...") for direct use with re.finditer()
# against binary data.
#
# Naming convention:
#   "siemens_part"         — Siemens 5WK9 ECU part number
#   "family"               — MS43 family string
#   "ident_record"         — full identification record line
#   "calibration_dataset"  — caNNNNN.DAT calibration dataset filename
#   "program_number"       — standalone bounded program-number run (fallback)
#   "serial_number"        — 10-digit production serial
#   "oem_part_number"      — 7-digit BMW/OEM part number
# ---------------------------------------------------------------------------

PATTERNS: Dict[str, bytes] = {
    # ------------------------------------------------------------------
    # Hardware identification
    # ------------------------------------------------------------------
    # Siemens hardware part number — "5WK90027" (5WK9 + 4–6 digits)
    "siemens_part": rb"5WK9\d{4,6}",
    # Full ident record — part + serial + family anchor
    # e.g. "5WK90027--1061330037MS430604..."  (verified at 0x3F80)
    # This is a stable shape across the whole corpus.
    "ident_record": rb"5WK9\d{4,6}--\d{10}MS4[0-9]",
    # ------------------------------------------------------------------
    # Software / calibration identification
    # ------------------------------------------------------------------
    # Family string — literal MS43 (unique positive signature)
    "family": rb"MS43",
    # Calibration dataset filename — "ca<num>.DAT" (at 0x70040)
    # e.g. "ca430069.DAT"  "ca43003701.DAT"  "ca4300056010000.DAT"
    # The 43 prefix keeps it MS43-specific; the capture group IS the
    # program number (the community software identifier).
    "calibration_dataset": rb"ca43\d+\.DAT",
    # Program number — standalone bounded run, documented fallback only.
    # Public catalogs show 6–13 digit variants; "43" + 4–12 digits bounded
    # by non-digits is the safe shape.  On the real corpus this fires at
    # 0x70042 inside "ca430069.DAT" (value "430069").
    "program_number": rb"(?<!\d)43\d{4,12}(?!\d)",
    # Serial number — 10-digit production serial from the ident record.
    # First hit in the ident area is "1061330037" at 0x3F8A.
    "serial_number": rb"(?<!\d)\d{10}(?!\d)",
    # OEM part number — 7-digit BMW/OEM part (verified absent on corpus).
    "oem_part_number": rb"(?<!\d)\d{7}(?!\d)",
}

# ---------------------------------------------------------------------------
# Search regions
# ---------------------------------------------------------------------------
# Based on analysis of real Siemens MS43 binaries.  All known files are
# exactly 512 KB (524288 bytes).
#
# Key findings:
#   - The ident record (5WK9 + serial + MS43) lives at 0x3F80
#   - The calibration dataset reference sits at 0x70040 — PAST the 128 KB
#     extended region, so it is searched in the full binary.
#   - The program number and OEM part live in the tail (0x6FE80–0x70040).
# ---------------------------------------------------------------------------

SEARCH_REGIONS: Dict[str, slice] = {
    # First 4 KB — ident record, hardware number, serial, SW-ID area
    "header": slice(0x0000, 0x1000),
    # First 64 KB — ident record fields, serial number, family literal
    "ident_area": slice(0x0000, 0x10000),
    # First 128 KB — positive-detection scan window for can_handle()
    "extended": slice(0x0000, 0x20000),
    # Full binary — calibration dataset, program number, OEM part number
    "full": slice(0x0000, None),
}

# ---------------------------------------------------------------------------
# Pattern → search region mapping
# ---------------------------------------------------------------------------
# Defines which region each pattern is searched in.
# Narrower regions = faster search = lower false-positive rate.
# ---------------------------------------------------------------------------

PATTERN_REGIONS: Dict[str, str] = {
    # Ident area (64 KB) — primary ident fields
    "siemens_part": "ident_area",
    "ident_record": "ident_area",
    "family": "ident_area",
    "serial_number": "ident_area",
    # Full binary — fields that live in the tail (past 0x20000)
    "calibration_dataset": "full",
    "program_number": "full",
    "oem_part_number": "full",
}

# ---------------------------------------------------------------------------
# Detection signatures
# ---------------------------------------------------------------------------
# Byte sequences used by SiemensMS43Extractor.can_handle() to quickly
# detect whether a binary belongs to a Siemens MS43 ECU.
#
# MS43 is PRIMARY — it is unique to this family.  5WK9 is SECONDARY: it is
# shared with Simtec56 (128 KB), which the 524288-byte size gate rejects.
# The size gate (exactly 524288 bytes) is checked first as a fast pre-filter.
# ---------------------------------------------------------------------------

DETECTION_SIGNATURES: list[bytes] = [
    b"MS43",  # family literal — unique positive anchor
    b"5WK9",  # Siemens hardware part prefix — secondary (shared w/ Simtec56)
]

# ---------------------------------------------------------------------------
# Exclusion signatures
# ---------------------------------------------------------------------------
# Byte sequences that indicate the binary belongs to a DIFFERENT ECU family.
# If ANY of these is found in the binary, can_handle() returns False even if
# detection signatures are present.
#
# This prevents false positives when:
#   - A Bosch binary happens to contain an "MS43" or "5WK9" byte sequence
#     in calibration data regions
#   - A different Siemens family (SID801/SID803/PPD/SIMOS/Simtec56/EMS2000)
#     happens to contain one of the MS43 detection signatures
# ---------------------------------------------------------------------------

EXCLUSION_SIGNATURES: list[bytes] = [
    b"EDC17",  # Bosch EDC17 family
    b"MEDC17",  # Bosch MEDC17 family
    b"MED17",  # Bosch MED17 family
    b"ME7.",  # Bosch ME7.x family
    b"ME9",  # Bosch ME9 family
    b"SID803",  # Siemens SID803/SID803A — separate extractor
    b"SID801",  # Siemens SID801/SID801A — separate extractor
    b"5WS4",  # Siemens SID801/SID801A hardware prefix
    b"PPD",  # Siemens PPD1.x diesel family
    b"SIMOS",  # Siemens SIMOS family
    b"BOSCH",  # generic Bosch marker
]

# ---------------------------------------------------------------------------
# File size constant
# ---------------------------------------------------------------------------
# All known MS43 binaries are exactly 512 KB.  This is used as a fast
# pre-filter in can_handle() and is what disambiguates MS43 (524288) from
# Simtec56 (131072) — both share the 5WK9 hardware prefix.
# ---------------------------------------------------------------------------

MS43_FILE_SIZE: int = 524288  # 512 KB = 0x80000

# ---------------------------------------------------------------------------
# Identity offset constants (verified on the real corpus, 2026-08-27)
# ---------------------------------------------------------------------------
# All 4 corpus files are identical at these offsets — safe to hard-code.
# ---------------------------------------------------------------------------

# Ident record region — Siemens part + serial + MS43 family literal
IDENT_BLOCK: tuple = (0x3F80, 0x3FC0)
# SW-ID area — 12 ASCII digits ("000000115852"); boot checksum init source
SWID_OFFSET: int = 0x3C34
# Tail ident block — program number sits 8 bytes in (6 ASCII digits)
PROGRAM_NUMBER_OFFSET: int = 0x6FFBA
# SW-number tail — 11 ASCII digits ("96577355117"); cal checksum init source
SW_NUMBER_TAIL_OFFSET: int = 0x6FFBF
