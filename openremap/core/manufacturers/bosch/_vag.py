"""
Shared VAG part-number scanner used by the Bosch family extractors.

VAG (VW/Audi/Seat/Skoda) software part numbers look like:
    "038906016L", "03G906016AC", "045906019BQ", "06A906032LP",
    "4B0906018DF", "3B0907401", "022906032CS", "074906018C"

Structure: a 2-char family/engine code + letter-or-digit + 6 digits +
optional 1-2 trailing letters (spaces allowed between groups).

The distinguishing rule vs. everything else in a dump: a part number must
contain at least one LETTER.  Pure-digit runs are Bosch HW numbers
("0261xxxxxx", "0281xxxxxx"), Bosch SW numbers ("1037xxxxxx") or
calibration codes ("037906259") — never part numbers.  Those are *skipped*,
not treated as the end of the search: see find_vag_part_number().
"""

import re

# Lead char covers the observed family/engine codes: 0 (03G/038/045/06A/022/
# 023/03L/074...), 3 (3B...) and 4 (4B...).  The trailing-letter group is
# optional in the pattern (3B0907401 has none); the letter-content guard in
# find_vag_part_number() is what keeps all-digit runs out.
_VAG_PN_RE = re.compile(
    rb"(?<![A-Z0-9])[034][0-9A-Z]{2}[\s]?\d{3}[\s]?\d{3}"
    rb"(?:[\s]?[A-Z]{1,2})?(?![A-Z0-9])"
)


def find_vag_part_number(data: bytes):
    """
    Return the first VAG part number found anywhere in *data*, or None.

    Normalises the match by stripping internal spaces.  All-digit matches
    (HW/SW/calibration numbers) are rejected by the letter-content guard, and
    the scan **continues past them**: a calibration code of the same shape can
    sit earlier in the dump than the real part number, and aborting on the
    first structural match would report "no part number" for a binary that has
    one.
    """
    for m in _VAG_PN_RE.finditer(data):
        val = m.group(0).decode("ascii", errors="ignore").replace(" ", "").strip()
        if re.search(r"[A-Z]", val):
            return val
    return None
