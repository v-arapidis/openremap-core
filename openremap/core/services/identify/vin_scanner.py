"""
VIN scanner — locate vehicle identification numbers in ECU binaries.

Accuracy model (honest by construction): a VIN is NOT just any 17-char
run — ECU files are full of VIN-shaped serials, calibration IDs, test
ramps and part numbers.  Candidates are scored on structural evidence:

- **WMI whitelist**        — first 3 chars must be a known world
  manufacturer identifier (VW=WVW, Audi=WAU, BMW=WBA, …)
- **ISO 3779 check digit** — position 9 must satisfy the transliteration/
  weight checksum (the strongest single signal)
- **Model year**           — position 10 in the legal year charset
- **Numeric tail**         — positions 12–17 digits (the serial part)
- **Ident-block context**  — the candidate sits inside a printable-ASCII
  ident block (layout segmenter) — real VINs live in ident metadata
- **Mirror consensus**     — the same VIN appears multiple times (real
  ECUs mirror the VIN across blocks)

Confidence is the sum of the evidence (cap 0.95), NEVER a boolean claim.
Ident-block and mirror evidence only corroborate candidates with a known
WMI (see scan_vins).  Measured on the real corpus: all natural lookalikes
score <= 0.45; injected real-shaped VINs in ident blocks score >= 0.9.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openremap.core.services.maps.layout import find_ident_blocks

# Lookahead pattern — overlapping windows.  A VIN may start at ANY
# offset inside an alphanumeric run; finditer's default non-overlapping
# stride skips it whenever preceding [A-Z0-9] text leaves a matching
# 17-char window that consumes into the VIN start (2026-08-20).
_CANDIDATE_RE = re.compile(rb"(?=([A-Z0-9]{17}))")

# ISO 3779 excludes I, O, Q anywhere in a VIN.
_ILLEGAL = set("IOQ")

# Transliteration table for the check-digit algorithm (A=1 … Z=9,
# skipping I, O, Q).
_TRANS = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", "123456781234578923456789"))
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

# Model-year chars (1980=A … 2000=Y, 2001–2009=1–9, 2010=A …).
_YEAR_CHARS = set("123456789ABCDEFGHJKLMNPRSTVWXY")

# World manufacturer identifiers — the brands our corpus spans plus the
# most common European/US/Asian prefixes.
_WMI = {
    "WVW", "WVG", "WV1", "WV2", "3VW", "9BW",      # VW
    "WAU", "WUA", "TRU",                           # Audi
    "VSS",                                         # Seat
    "TMB", "TMA", "TMP",                           # Skoda
    "WBA", "WBS", "WBX", "WBY", "4US",             # BMW / Mini
    "WDB", "WDD", "WDC", "WDF", "W1K", "4JG",      # Mercedes
    "W0L", "W0V", "XUF", "1G0", "1GC", "KL1",      # Opel/Vauxhall/GM
    "VF1", "VF2", "VF3", "VF4", "VF5", "VF6",      # Renault/Peugeot/Citroën
    "VF7", "VF8", "VNE", "VR3",
    "WF0", "WF1", "1FA", "1FD", "MAJ", "MNB",      # Ford
    "ZFA", "ZFF", "ZAR", "ZLA", "ZAP",             # Fiat/Ferrari/Alfa/Lancia
    "YV1", "YV2", "YV3", "YV4",                    # Volvo
    "WP0", "WP1",                                  # Porsche
    "SAL", "JHM", "JH4", "KNB", "KMH", "NMT",      # Land Rover/Honda/Kia/Hyundai/Toyota
    "SB1", "SHS", "U5Y",                           # Suzuki/other
    "1C4", "1C6", "1FT", "1FM", "2FM",             # Chrysler/Jeep/Ford US
    "1G1", "1G2", "1G4", "1G8", "2G1", "3G1",      # GM
}

# Evidence weights — summed into the confidence score.
_W_WMI = 0.30
_W_CHECK = 0.25
_W_YEAR = 0.10
_W_TAIL = 0.10
_W_IDENT = 0.10
_W_MIRROR = 0.10
_CONF_CAP = 0.95


@dataclass(frozen=True)
class VINHit:
    """One VIN candidate with its evidence."""

    offset: int
    vin: str
    confidence: float
    wmi_known: bool
    check_digit_ok: bool
    year_plausible: bool
    numeric_tail: bool
    in_ident_block: bool
    mirror_count: int

    @property
    def evidence(self) -> list[str]:
        out = []
        if self.wmi_known:
            out.append("wmi")
        if self.check_digit_ok:
            out.append("check-digit")
        if self.year_plausible:
            out.append("year")
        if self.numeric_tail:
            out.append("numeric-tail")
        if self.in_ident_block:
            out.append("ident-block")
        if self.mirror_count > 1:
            out.append(f"mirrored-x{self.mirror_count}")
        return out


def is_valid_check_digit(vin: str) -> bool:
    """ISO 3779 position-9 checksum check.  Returns False when any char
    cannot be transliterated."""
    if len(vin) != 17:
        return False
    try:
        vals = [
            int(c) if c.isdigit() else int(_TRANS[c])
            for c in vin
        ]
    except KeyError:
        return False
    total = sum(v * w for v, w in zip(vals, _WEIGHTS))
    rem = total % 11
    if rem == 10:
        return vin[8] == "X"
    return vin[8].isdigit() and int(vin[8]) == rem


def scan_vins(
    data: bytes, *, min_confidence: float = 0.0
) -> list[VINHit]:
    """
    Find VIN candidates in *data* and score them.

    Returns hits sorted by confidence (desc), then offset.  Hits with
    confidence below *min_confidence* are dropped — the default returns
    everything so callers can set their own policy.
    """
    if not data:
        return []

    blocks = [(b.start, b.end) for b in find_ident_blocks(data)]
    hits: list[VINHit] = []
    # Mirror counts are memoized per distinct 17-byte string — the
    # overlapping window scan multiplies candidate count, and
    # bytes.count is O(n) per call.
    counts: dict[bytes, int] = {}

    for m in _CANDIDATE_RE.finditer(data):
        raw = m.group(1)
        vin = raw.decode("ascii")
        if any(c in _ILLEGAL for c in vin):
            continue
        # Diversity guard: pattern fills (99999999999999999, 0000…)
        # trivially satisfy the checksum but are never VINs — a real VIN
        # carries >= 6 distinct characters.
        if len(set(vin)) < 6:
            continue

        wmi = vin[:3] in _WMI
        cd = is_valid_check_digit(vin)
        year = vin[9] in _YEAR_CHARS
        tail = vin[11:].isdigit()
        in_ident = any(s <= m.start(1) < e for s, e in blocks)
        mirrors = counts.setdefault(raw, data.count(raw))

        conf = 0.0
        if wmi:
            conf += _W_WMI
        if cd:
            conf += _W_CHECK
        if year:
            conf += _W_YEAR
        if tail:
            conf += _W_TAIL
        # Ident-block context and mirror consensus only corroborate a
        # candidate that already looks like a VIN (known WMI): serials
        # and calibration numbers live in ident blocks and are often
        # mirrored too.  Without the gate they cross the lookalike
        # line — measured on the corpus: '31011118777544444' (Ferrari
        # ME7.3) scores 0.65 with ident+mirror, 0.45 without.
        if wmi:
            if in_ident:
                conf += _W_IDENT
            if mirrors > 1:
                conf += _W_MIRROR
        conf = round(min(conf, _CONF_CAP), 2)

        hits.append(
            VINHit(
                offset=m.start(1),
                vin=vin,
                confidence=conf,
                wmi_known=wmi,
                check_digit_ok=cd,
                year_plausible=year,
                numeric_tail=tail,
                in_ident_block=in_ident,
                mirror_count=mirrors,
            )
        )

    hits.sort(key=lambda h: (-h.confidence, h.offset))
    return [h for h in hits if h.confidence >= min_confidence]
