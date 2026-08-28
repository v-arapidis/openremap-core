"""
Unified binary analysis — ``openremap analyze``.

One pass that describes a whole ECU binary: identity + confidence, VIN,
flash layout, map discovery, checksums, and the health verdict.  Composes
the existing domain services — this is the "reverse-engineering mode"
(roadmap item 10) whose prototype was ``health_report``; this module is
the descriptive (non-gate) presentation of the same services.

Pure ``bytes``-in/``dict``-out (``container`` is an optional display
hint passed by the CLI input boundary).

Composition notes:
  - The map scan (axes → tables) is the expensive step (~6 s on 4 MB);
    its output is handed to ``segment()`` via the ``tables`` kwarg so the
    layout step does NOT rescan.
  - ``health_report`` is reused wholesale for the verdict section; it
    re-scans internally (identity/checksums/map-count) — accepted for v1
    (possible future optimisation: thread precomputed results into it).
  - ``--fast`` (CLI) skips maps + checksums + the health verdict.
"""

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

from openremap.core.services.checksums.checksum import sweep, verify_me7
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import (
    detect_all as detect_ironfelix,
)
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.coherence import CoherenceReport, check_coherence
from openremap.core.services.health import HealthReport, health_report
from openremap.core.services.identify.confidence import (
    ConfidenceResult,
    score_identity,
)
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.arch import arch_for_family, decoder_label
from openremap.core.arch.detect import detect_arch
from openremap.core.arch.refs import XrefReport, collect_xrefs
from openremap.core.services.maps.layout import (
    Region,
    code_regions_from_layout,
    find_ident_blocks,
    segment,
)
from openremap.core.services.maps.map_hunter import MapTable, scan_map_axes, scan_map_tables
from openremap.core.services.maps.xrefs import (
    _table_spans,
    adjust_table_scores,
    xref_evidence,
)
from openremap.core.services.vin_decode import DecodedVIN, decode_vin

#: VIN floor — same as ``identify`` (measured: 2/1,871 corpus files qualify).
_VIN_FLOOR = 0.6

#: JSON/human caps — keep reports bounded (scan_map_tables can return 8k).
_MAX_TABLES_JSON = 50
_MAX_TABLES_HUMAN = 5


@dataclass
class AnalyzeReport:
    """The full descriptive report for one binary."""

    file_size: int
    sha256: str
    container: str  # "raw binary" | "Intel HEX" | "Motorola S-Record"
    identity: dict
    confidence: ConfidenceResult
    vin: Optional[DecodedVIN]
    vin_confidence: Optional[float]
    endian: str
    cell_bytes: int
    coherence: Optional[CoherenceReport] = None
    regions: List[Region] = field(default_factory=list)
    ident_blocks: List[Region] = field(default_factory=list)
    tables: List[MapTable] = field(default_factory=list)
    axis_count: int = 0
    xrefs: Optional[XrefReport] = None
    checksums: Optional[dict] = None
    health: Optional[HealthReport] = None
    fast: bool = False

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        """JSON-safe representation (stdlib-json serialisable)."""
        vin = None
        if self.vin is not None:
            vin = {
                "candidate": self.vin.vin,
                "confidence": self.vin_confidence,
                "manufacturer": self.vin.manufacturer,
                "region": self.vin.region,
                "country": self.vin.country,
                "years": self.vin.years,
                "checksum_valid": self.vin.checksum_valid,
                "decoded": self.vin.decoded,
            }
        top_tables = sorted(self.tables, key=lambda t: t.score, reverse=True)[
            :_MAX_TABLES_JSON
        ]
        xr = self.xrefs
        return {
            "container": self.container,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "identity": self.identity,
            "confidence": {
                "score": self.confidence.score,
                "tier": self.confidence.tier,
                "signals": [
                    {"delta": s.delta, "label": s.label}
                    for s in self.confidence.signals
                ],
                "warnings": self.confidence.warnings,
            },
            "coherence": (
                self.coherence.to_dict() if self.coherence is not None else None
            ),
            "vin": vin,
            "hardware": {"endian": self.endian, "cell_bytes": self.cell_bytes},
            "layout": {
                "regions": [
                    {
                        "kind": r.kind,
                        "start": r.start,
                        "end": r.end,
                        "size": r.size,
                        "confidence": round(r.confidence, 2),
                        "tables": r.tables,
                        "tables_high_conf": r.tables_high_conf,
                    }
                    for r in self.regions
                ],
                "ident_blocks": [
                    {"start": r.start, "end": r.end} for r in self.ident_blocks
                ],
            },
            "xrefs": (
                {
                    "status": xr.status,
                    "skip_reason": xr.skip_reason,
                    "arch": xr.arch,
                    "decoder": decoder_label(xr.arch),
                    "arch_source": (
                        "declared"
                        if arch_for_family(
                            self.identity.get("manufacturer"),
                            self.identity.get("ecu_family"),
                        )
                        is not None
                        else "detected"
                    ),
                    "endian": xr.endian,
                    "base_address": xr.base_address,
                    "code_bytes_scanned": xr.code_bytes_scanned,
                    "insn_count": xr.insn_count,
                    "reference_count": len(xr.referenced),
                }
                if xr is not None
                else None
            ),
            "maps": {
                "axis_count": self.axis_count,
                "table_count": len(self.tables),
                "tables": [
                    {
                        "offset": t.offset,
                        "cols": t.cols,
                        "rows": t.rows,
                        "cell_width": t.cell_width,
                        "byte_order": t.byte_order,
                        "score": round(t.score, 3),
                        "stride": t.stride,
                        "xref": xref_evidence(t, xr) if xr is not None else {},
                    }
                    for t in top_tables
                ],
            },
            "checksums": self.checksums,
            "health": (
                {
                    "checks": [
                        {"name": c.name, "status": c.status, "message": c.message}
                        for c in self.health.checks
                    ],
                    "healthy": self.health.healthy,
                }
                if self.health is not None
                else None
            ),
            "fast": self.fast,
        }


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def analyze_binary(
    data: bytes,
    filename: str = "unknown.bin",
    *,
    fast: bool = False,
    skip_maps: bool = False,
    container: str = "raw binary",
) -> AnalyzeReport:
    """Analyse *data* and return the full descriptive report.

    ``fast`` skips the map scan, checksums, and the health verdict.
    ``skip_maps`` skips only the map scan (keeps checksums + health).
    """
    ident = identify_ecu(data, filename)

    vins = scan_vins(data, min_confidence=_VIN_FLOOR)
    vin_top = vins[0] if vins else None
    vin_dec = decode_vin(vin_top.vin) if vin_top else None

    regions: List[Region] = []
    ident_blocks: List[Region] = []
    tables: List[MapTable] = []
    axis_count = 0
    xrefs: Optional[XrefReport] = None
    if not skip_maps and not fast:
        axes = scan_map_axes(data)
        axis_count = len(axes)
        tables = scan_map_tables(data, axes=axes)
        regions = segment(data, tables=tables)
        ident_blocks = find_ident_blocks(data)
        # Code-reference signal: disassemble the code regions, find tables
        # whose data is statically referenced by code, and apply the bonus.
        # Unknown/unmapped families fall through to the CPU-detection
        # cascade (detect_arch) instead of skipping the pass.
        arch_info = arch_for_family(
            ident.get("manufacturer"), ident.get("ecu_family")
        )
        codes = code_regions_from_layout(regions)
        spans = _table_spans(tables)
        if arch_info is None:
            xrefs = detect_arch(data, codes, ident.get("ecu_endian"), spans)
        else:
            xrefs = collect_xrefs(
                data,
                codes,
                arch_info,
                ident.get("ecu_endian"),
                spans=spans,
            )
        if xrefs.status == "ok":
            tables = adjust_table_scores(tables, xrefs)

    checksums = None
    if not fast:
        checksums = _summarize_checksums(data)

    # Coherence: cross-check identity vs checksum-detected family vs xref
    # arch, then feed it into the confidence score.  In ``--fast`` /
    # ``--skip-maps`` the checksum/xref passes never ran → coherence is
    # None and ``score_identity`` behaves exactly as before.
    coherence = None
    if not fast and not skip_maps:
        coherence = check_coherence(ident, checksums, xrefs)
    conf = score_identity(
        ident, filename=filename, data=data, coherence=coherence
    )

    health = health_report(data, filename) if not fast else None

    return AnalyzeReport(
        file_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        container=container,
        identity=ident,
        confidence=conf,
        coherence=coherence,
        vin=vin_dec,
        vin_confidence=vin_top.confidence if vin_top else None,
        endian=ident.get("ecu_endian", "little"),
        cell_bytes=ident.get("ecu_cell_bytes", 2),
        regions=regions,
        ident_blocks=ident_blocks,
        tables=tables,
        axis_count=axis_count,
        xrefs=xrefs,
        checksums=checksums,
        health=health,
        fast=fast,
    )


def _summarize_checksums(data: bytes) -> dict:
    """Compact checksum summary: swept schemes + per-detector verdicts.

    Covers the detector inventory mirrored from ``health._check_checksums``:
    ME7 (verify_me7), MS43 CRC16, Denso descriptor table, IronFelix
    profiles — so the coherence check sees every family detector.  Additive
    only: the existing ``schemes``/``me7``/``denso`` keys are unchanged;
    ``ms43``/``ironfelix`` appear only when the detector fires.
    """
    out: dict = {
        "schemes": [],
        "me7": None,
        "denso": None,
        "ms43": None,
        "ironfelix": [],
    }
    for m in sweep(data):
        out["schemes"].append(
            {
                "algo": m.scheme.algo,
                "region": m.scheme.region,
                "pages": f"{m.pages_matched}/{m.pages_total}",
                "rate": round(m.rate, 3),
            }
        )
    me7 = verify_me7(data)
    if me7 is not None:
        out["me7"] = {"status": me7.status, "scheme": me7.scheme.label}
    denso = detect_denso(data)
    if denso is not None:
        out["denso"] = {
            "table_offset": denso.table_offset,
            "ok": denso.ok,
            "total": len(denso.entries),
        }
    ms43 = detect_ms43(data)
    if ms43 is not None:
        out["ms43"] = {"ok": ms43.ok, "total": ms43.total}
    iron = detect_ironfelix(data)
    if iron:
        out["ironfelix"] = [
            {"description": p.description, "ok": p.ok, "total": p.total}
            for p in iron
        ]
    return out
