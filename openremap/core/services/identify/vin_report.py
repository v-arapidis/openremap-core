"""
openremap.core.services.identify.vin_report — VIN scan report (bytes → dict).

The bytes→dict ``scan-vins`` core: locate VIN candidates and score them,
returning the CLI ``--json``-shaped dict.  (The ``scan_vins`` RPC handler does
the path I/O.)
"""

from __future__ import annotations

from openremap.core.serialize import to_json_safe
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.services.vin_decode import decode_vin


def scan_vins_binary(data: bytes, filename: str, *, min_confidence: float = 0.4) -> dict:
    """Scan *data* for VIN candidates and return the JSON-safe dict."""
    hits = scan_vins(data, min_confidence=min_confidence)
    decoded = [decode_vin(h.vin) for h in hits]
    return to_json_safe(
        {
            "file": filename,
            "file_size": len(data),
            "candidates": [
                {
                    "offset": h.offset,
                    "vin": h.vin,
                    "confidence": h.confidence,
                    "evidence": h.evidence,
                    "mirror_count": h.mirror_count,
                    "manufacturer": d.manufacturer,
                    "region": d.region,
                    "country": d.country,
                    "years": d.years,
                    "checksum_valid": d.checksum_valid,
                    "decoded": d.decoded,
                }
                for h, d in zip(hits, decoded)
            ],
        }
    )
