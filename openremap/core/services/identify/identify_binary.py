"""
openremap.core.services.identify.identify_binary — binary-level identify.

The bytes→dict identify core: identity + confidence + top VIN candidate,
returning the CLI ``--json``-shaped dict.  Pure bytes-in / dict-out — no file
I/O; the caller supplies the friendly container name so this stays
transport-agnostic.  (The ``identify`` RPC handler does the path I/O +
container detection.)
"""

from __future__ import annotations

from openremap.core.serialize import to_json_safe
from openremap.core.services.identify.confidence import score_identity
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.services.vin_decode import decode_vin


def identify_binary(data: bytes, filename: str, container: str) -> dict:
    """Identify *data*, returning the CLI ``--json``-shaped dict.

    Pure bytes-in / dict-out — no file I/O.  The caller supplies the friendly
    container name (already decoded) so this stays transport-agnostic.
    """
    result = identify_ecu(data=data, filename=filename)
    confidence = score_identity(result, filename=filename, data=data)

    # Vehicle identity is orthogonal to ECU identity and NEVER part of the
    # match key.  Only the top candidate at >= 0.6 is kept (measured on the
    # real corpus: 2/1871 files qualify — real dealer-flashed VINs).
    vin_top = None
    vin_decoded = None
    vins = scan_vins(data, min_confidence=0.6)
    if vins:
        vin_top = vins[0]
        vin_decoded = decode_vin(vin_top.vin)

    out = dict(result)
    out["container"] = container
    out["confidence"] = {
        "score": confidence.score,
        "tier": confidence.tier,
        "signals": [{"delta": s.delta, "label": s.label} for s in confidence.signals],
        "warnings": confidence.warnings,
    }
    out["vin"] = (
        {
            "candidate": vin_top.vin,
            "confidence": vin_top.confidence,
            "manufacturer": vin_decoded.manufacturer,
            "region": vin_decoded.region,
            "country": vin_decoded.country,
            "years": vin_decoded.years,
            "checksum_valid": vin_decoded.checksum_valid,
            "decoded": vin_decoded.decoded,
        }
        if vin_top is not None
        else None
    )
    return to_json_safe(out)
