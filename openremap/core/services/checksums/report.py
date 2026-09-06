"""
openremap.core.services.checksums.report — checksum detection report.

Runs every family detector plus the generic sweep and serialises the results
into the CLI ``--json``-shaped dict.  Pure bytes-in / dict-out; the
``checksum`` RPC handler (path I/O) stays in the API gate.
"""

from __future__ import annotations

from openremap.core.serialize import to_json_safe
from openremap.core.services.checksums.checksum import (
    detect_me7_multipoint,
    detect_me7_multipoint_unverified,
    sweep,
    verify_me7,
)
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import detect_all as detect_ironfelix
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.checksums.nefmoto import (
    detect_me7_multirange,
    detect_me7_rolling,
)


def checksum_binary(data: bytes, filename: str) -> dict:
    """Detect checksum schemes in *data* and return the JSON-safe dict."""
    matches = sweep(data)
    me7 = verify_me7(data)
    mp_valid = detect_me7_multipoint(data) if me7 is not None else []
    mp_bootrom = (
        sum(1 for b in detect_me7_multipoint_unverified(data) if b.start < 0x20000)
        if me7 is not None
        else 0
    )
    ironfelix = detect_ironfelix(data)
    rolling = detect_me7_rolling(data)
    multirange = detect_me7_multirange(data)
    ms43 = detect_ms43(data)
    denso = detect_denso(data)

    return to_json_safe(
        {
            "file": filename,
            "file_size": len(data),
            "me7_main": (
                {
                    "status": me7.status,
                    "stored": me7.stored_hex,
                    "expected": me7.expected_hex,
                }
                if me7 is not None
                else None
            ),
            "me7_multipoint": (
                {
                    "valid": len(mp_valid),
                    "bootrom_unverifiable": mp_bootrom,
                }
                if me7 is not None
                else None
            ),
            "me7_rolling": (
                [
                    {
                        "store": e.store_offset,
                        "status": e.status,
                        "stored": f"{e.stored:08X}",
                        "expected": f"{e.expected:08X}",
                        "ranges": [
                            {"start": r.start, "end": r.end} for r in e.ranges
                        ],
                        "init_range": (
                            {"start": e.init_range.start, "end": e.init_range.end}
                            if e.init_range is not None
                            else None
                        ),
                    }
                    for e in rolling
                ]
                if rolling is not None
                else None
            ),
            "me7_multirange": (
                {
                    "store": multirange.store_offset,
                    "status": multirange.status,
                    "stored": f"{multirange.stored:08X}",
                    "expected": f"{multirange.expected:08X}",
                    "ranges": [
                        {"start": r.start, "end": r.end} for r in multirange.ranges
                    ],
                }
                if multirange is not None
                else None
            ),
            "ms43": (
                {
                    "crcs": [
                        {
                            "name": c.name,
                            "slot": c.slot,
                            "status": c.status,
                            "stored": f"{c.stored:04X}" if c.stored is not None else None,
                            "expected": f"{c.expected:04X}" if c.expected is not None else None,
                            "blocks": [{"start": s, "end": e} for s, e in c.blocks],
                        }
                        for c in ms43.crcs
                    ],
                    "mons": [
                        {"name": m.name, "slot": m.slot, "status": m.status,
                         "stored": f"{m.stored:08X}" if m.stored is not None else None}
                        for m in ms43.mons
                    ],
                    "ok": ms43.ok,
                    "total": ms43.total,
                }
                if ms43 is not None
                else None
            ),
            "denso": (
                {
                    "table": denso.table_offset,
                    "status": denso.status,
                    "ok": denso.ok,
                    "total": denso.total,
                    "entries": [
                        {
                            "index": e.index,
                            "start": e.start,
                            "end": e.end,
                            "status": e.status,
                            "stored": f"{e.stored:08X}" if e.stored is not None else None,
                            "expected": f"{e.expected:08X}" if e.expected is not None else None,
                        }
                        for e in denso.entries
                    ],
                }
                if denso is not None
                else None
            ),
            "ironfelix": [
                {
                    "family": p.family,
                    "description": p.description,
                    "subtype": p.subtype,
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "stored": c.stored_hex,
                            "expected": c.expected_hex,
                        }
                        for c in p.checks
                    ],
                    "checks_ok": p.ok,
                    "checks_total": p.total,
                    "multipoint_valid": p.multipoint_valid,
                    "multipoint_unverified": p.multipoint_unverified,
                }
                for p in ironfelix
            ],
            "schemes": [
                {
                    "algo": m.scheme.algo,
                    "init": m.scheme.init,
                    "final_xor": m.scheme.final_xor,
                    "region": m.scheme.region,
                    "exclude_tail": m.scheme.exclude_tail,
                    "store": m.scheme.store,
                    "store_le": m.scheme.store_le,
                    "complement": m.scheme.complement,
                    "pages_matched": m.pages_matched,
                    "pages_total": m.pages_total,
                    "rate": round(m.rate, 3),
                }
                for m in matches
            ],
        }
    )
