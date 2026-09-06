"""
Shared helpers for Denso ECU extractors.

The Denso Subaru firmware family (68HC16Y5 / SH7055 / SH7058 and the EE20
diesel unit) stores an 8-character "calibration ID" (CAL ID) in plain
ASCII in an identity block near the start of the binary.  The Subaru
tuning community (RomRaider / ECUFlash definitions) uses this CAL ID as
the primary software identifier, so every Denso extractor resolves it
into ``software_version``.

CAL ID character shape observed across 500+ factory ROMs:

    1-2 uppercase letters, 1 digit, 3-6 letters/digits (lowercase allowed)

    A2WC400H   A4RG060P   AZ1J500T   EP5F400c   A8DH20

This module provides:

  - ``CAL_ID_PATTERN`` / ``cal_id_match()`` — strict positional CAL-ID test
  - ``DensoFamily`` — family names shared by the Denso extractors
  - ``DENSO_ANCHORS`` — the Denso copyright strings found in ROMs
"""

import re

# ---------------------------------------------------------------------------
# CAL ID pattern
# ---------------------------------------------------------------------------
# 1-2 uppercase letters, a digit, then 3-6 alphanumerics (lowercase allowed).
# Anchored so a match spans the whole field (6-8 characters total).
# ---------------------------------------------------------------------------

CAL_ID_PATTERN = re.compile(rb"[A-Z]{1,2}[0-9][A-Za-z0-9]{3,6}")

#: Minimum CAL ID length accepted during detection (8 is the norm; a few
#: ROMs store a truncated 6-character ID padded with spaces).
MIN_CAL_ID_LENGTH = 6

#: Maximum CAL ID length (8 is the norm).
MAX_CAL_ID_LENGTH = 8


def cal_id_match(field: bytes) -> str | None:
    """
    Test a byte field as a Denso Subaru CAL ID.

    Returns the decoded string (trailing spaces stripped) when *field*
    looks like a CAL ID, else None.

    Padding behaviour: the field is typically exactly 8 bytes.  A few ROMs
    store a truncated 6-character ID followed by two space bytes (e.g. the
    ``A8DH2Z1Z`` file stores ``A8DH20``, the ``A2WC50`` file stores
    ``A2WC50``) — the padding is stripped before the shape test so the
    truncated ID is still accepted.  Note: for the ``A8DH2Z1Z`` file the
    filename romid (``A8DH2Z1Z``, 8 chars) does not match the stored CAL ID
    (``A8DH20``, 6 chars) — a known filename/label anomaly, not an extraction
    bug (the full 8-char string lives in a data area, not the ident block).
    """
    stripped = field.rstrip(b" ")
    if len(stripped) < MIN_CAL_ID_LENGTH or len(stripped) > MAX_CAL_ID_LENGTH:
        return None
    match = CAL_ID_PATTERN.fullmatch(stripped)
    if not match:
        return None
    return stripped.decode("ascii")


def find_cal_id(data: bytes, offset: int) -> str | None:
    """
    Read an 8-byte CAL ID field at *offset*.

    Returns the decoded, space-stripped CAL ID or None.
    """
    if offset + MAX_CAL_ID_LENGTH > len(data):
        return None
    return cal_id_match(data[offset : offset + MAX_CAL_ID_LENGTH])


def find_internal_id(data: bytes, offset: int) -> str | None:
    """
    Read the 8-byte internal ID field at *offset*.

    The internal ID (e.g. "86CAU_AT", "46VEU_STi") sits 9 bytes after the
    CAL ID in the A-layout identity blocks.  It may contain underscores,
    lowercase letters, and trailing spaces.  Returns the decoded, stripped
    string when all 8 bytes are printable ASCII, else None.
    """
    if offset + 8 > len(data):
        return None
    field = data[offset : offset + 8]
    if any(b < 0x20 or b > 0x7E for b in field):
        return None
    value = field.decode("ascii").rstrip()
    return value or None


# ---------------------------------------------------------------------------
# Denso copyright anchors found in the corpus
# ---------------------------------------------------------------------------

# Standard 32-bit A-layout anchor ("Copr.DENSO2011 ")
DENSO_COPR = b"Copr.DENSO"

# Diesel-only anchor — the diesel ID block spells it "Cpyr.DENSO".
DENSO_CPYR = b"Cpyr.DENSO"

# 16-bit 68HC16Y5 anchor ("CACopyrightDENSO2002")
DENSO_COPYRIGHT = b"CopyrightDENSO"


class DensoFamily:
    """ECU family names reported by the Denso extractors.

    Oracle-corrected 2026-09-02 (see the Denso Subaru relabel entry in
    ``changelog/0.7.6.md``): the Subaru petrol family labels were one CPU
    generation off.  The detection buckets were correct (each size maps to
    one clean CPU); only the names were shifted:

        HC16Y5  — 160/192 KB, 16-bit Motorola HC16  (was mislabelled SH7055)
        SH7055  — 512 KB, SuperH SH-2              (was mislabelled SH7058)
        SH7058  — 1 MB, SuperH SH-2                (was mislabelled SH72531)
    """

    HC16Y5 = "68HC16Y5"
    SH7055 = "SH7055"
    SH7058 = "SH7058"
    DIESEL = "Diesel"
