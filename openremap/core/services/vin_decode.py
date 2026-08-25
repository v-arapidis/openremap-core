"""
VIN decoding — enrich a VIN *candidate* with manufacturer/region/year info.

Detection stays the job of :mod:`vin_scanner` (structural evidence, scores,
"never a claim").  This module only DECODES an already-found candidate using
``vininfo`` (BSD-3): WMI → manufacturer, region, country, model years, and
the ISO 3779 check digit.

Honesty rules:
  - Decoding is permissive and NEVER raises — a malformed VIN or an unknown
    WMI yields ``decoded=False`` (all optional fields None), never a crash.
  - ``vininfo`` reports unknown WMIs as a literal "UnsupportedBrand" and can
    guess region/country for them — we treat that as *not decoded* and drop
    the guesswork rather than display it as fact.
  - The manufacturer is from vininfo's community/public database: callers
    must label it "decoded, unverified" (US/EU-centric; model detail is
    usually absent for VW/Audi/BMW).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from vininfo import Vin

#: vininfo's marker for an unknown world-manufacturer identifier.
_UNSUPPORTED = "UnsupportedBrand"


@dataclass(frozen=True)
class DecodedVIN:
    """Decoded details for one VIN candidate.  ``decoded=False`` → no info."""

    vin: str
    decoded: bool
    manufacturer: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    years: List[int] = field(default_factory=list)
    checksum_valid: bool = False


def decode_vin(vin: str) -> DecodedVIN:
    """Decode ``vin`` with vininfo; never raises.

    Returns ``DecodedVIN(vin, False, …)`` for malformed input or an unknown
    WMI (vininfo's ``ValidationError`` and its "UnsupportedBrand" marker are
    both swallowed).  ``checksum_valid`` is still reported when computable —
    it is a mechanical ISO 3779 check, independent of the WMI database.
    """
    try:
        v = Vin(vin)
    except Exception:
        return DecodedVIN(vin, False)

    try:
        manufacturer = v.manufacturer
        if (
            not isinstance(manufacturer, str)
            or not manufacturer
            or manufacturer == _UNSUPPORTED
        ):
            return DecodedVIN(vin, False, checksum_valid=bool(v.verify_checksum()))
        years = list(v.years) if getattr(v, "years", None) else []
        return DecodedVIN(
            vin=vin,
            decoded=True,
            manufacturer=manufacturer,
            region=getattr(v, "region", None) or None,
            country=getattr(v, "country", None) or None,
            years=years,
            checksum_valid=bool(v.verify_checksum()),
        )
    except Exception:
        return DecodedVIN(vin, False)
