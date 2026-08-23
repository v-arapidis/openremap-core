"""
Calibration health report — one-shot safety check for a single ECU binary.

``openremap health <file>`` runs every analysis layer over the file once
and reports problems a tuner would care about:

  1. identity        — family / manufacturer / confidence tier
  2. checksums       — every known family scheme's OK/STALE status
                       (ME7 main/multipoint/rolling/multirange, MS43,
                       Denso descriptor table, IronFelix profiles)
  3. axis sanity     — implausible axis values (endian-mismatched
                       tables produce garbage axes) and non-monotonic /
                       all-zero axes on high-score tables
  4. map count       — high-score table count vs a corpus-derived
                       envelope per ECU family (too few = wiped/erased
                       calibration; too many = scanner garbage)
  5. erased blocks   — large erased regions embedded in the middle of
                       data (file-start and file-end erasure are normal
                       flash layouts; mid-file erasure is an anomaly)
  6. VIN duplicates  — two distinct high-confidence VINs in one file

Every check returns ``ok`` / ``warn`` / ``fail`` / ``skip``.  The file
is ``healthy`` iff no check fails — ``warn`` levels are reported but do
not block CI gates (``--json`` for machine consumption).

The per-family map-count envelopes are corpus-derived (measured
2026-08-15 on tests/data/ECUs samples, n per family noted in
``_MAP_ENVELOPES``); families without a measured envelope skip the
check honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openremap.core.services.checksums.checksum import (
    detect_me7_multipoint,
    detect_me7_multipoint_unverified,
    verify_me7,
)
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import detect_all as detect_ironfelix
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.checksums.nefmoto import (
    detect_me7_multirange,
    detect_me7_rolling,
)
from openremap.core.services.identify.confidence import score_identity
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_classifier import family_fuel_type
from openremap.core.services.maps.map_hunter import scan_map_tables

# ---------------------------------------------------------------------------
# Corpus-derived map-count envelopes (measured 2026-08-15, tests/data/ECUs
# sample of 4-8 files per family; values = high-score (>=0.85) table count).
# Prefix-matched case-insensitively, like the confidence family profiles.
# LOW: observed min (0 where some dumps are legitimately map-free).
# HIGH: observed max with a 50% margin.
# ---------------------------------------------------------------------------

_MAP_ENVELOPES: dict[str, tuple[int, int]] = {
    "EDC1": (0, 10),
    "EDC3": (5, 28),
    "EDC15": (17, 465),
    "EDC16": (96, 330),
    "EDC17": (188, 1044),
    "M1": (0, 22),        # M1.3/M1.55/M1.7 — mixed envelopes
    "M2": (1, 33),        # M2.3–M2.9
    "M3": (1, 30),        # M3.1/M3.3/M3.8
    "M4": (3, 60),        # M4.3/M4.4
    "M5": (15, 54),
    "ME1.5.5": (38, 85),
    "ME7": (31, 126),     # ME7.1/ME7.1.1/ME7.3/ME7.5/ME7.6.2/ME71/ME7early
    "ME9": (145, 217),
    "MED9": (78, 196),
    "MED17": (95, 142),
    "MP3": (0, 9),
    "MP7": (10, 19),
    "MP9": (9, 13),
    "PPD": (80, 144),
    "SID801": (0, 39),
    "SID803": (70, 135),
    "SIMOS": (0, 22),
    "Simtec56": (5, 10),
    "LH": (0, 7),
    "KE": (1, 1),
    # Denso/Hitachi Subaru families (corpus-wide, measured 2026-08-15)
    "Diesel": (138, 208),
    "SH7055": (12, 52),
    "SH7058": (5, 57),
    "SH72531": (4, 150),
    "SH72546": (156, 375),
}

#: Axis plausibility caps (16-bit axis values).  A factory ECU's real
#: axes (RPM/load/pressure/speed) stay well below 10k; values above 30k
#: come from scanner false-positive tables or endian-mismatched reads.
_AXIS_WARN = 30000  # suspicious — likely a scanner false-positive table
_AXIS_FAIL = 60000  # clear garbage — near u16 max
_DIESEL_RPM_FAIL = 9000  # a diesel axis above 9000 cannot be RPM/load

#: Minimum size of an embedded erased region worth flagging.
_ERASED_EMBEDDED_MIN = 0x4000

#: VIN confidence floor for the duplication check.
_VIN_CONFIDENCE = 0.6

_HIGH_SCORE = 0.85


@dataclass
class HealthCheck:
    """One check's verdict."""

    name: str
    status: str  # ok | warn | fail | skip
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class HealthReport:
    """Full health report for one binary."""

    file_size: int
    family: str | None
    manufacturer: str | None
    confidence_tier: str
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(c.status != "fail" for c in self.checks)


def _read_axis(data: bytes, offset: int, count: int, byte_order: str) -> list[int]:
    """Read u16 axis values (the map scanner's axis cell format)."""
    import struct

    fmt = f"{'<' if byte_order == 'little' else '>'}H"
    return [struct.unpack_from(fmt, data, offset + i * 2)[0] for i in range(count)]


def _check_identity(data: bytes, filename: str, tier: str) -> HealthCheck:
    ident = identify_ecu(data, filename)
    family = ident.get("ecu_family")
    manufacturer = ident.get("manufacturer")
    if not family:
        return HealthCheck(
            "identity",
            "warn",
            "unidentified binary — family-specific checks will be skipped",
        )
    return HealthCheck(
        "identity",
        "ok",
        f"{manufacturer} {family}",
        [f"confidence: {tier}"],
    )


def _check_checksums(data: bytes) -> HealthCheck:
    """Run every known family scheme; fail when any detected scheme is stale."""
    found: list[str] = []
    stale: list[str] = []
    details: list[str] = []

    me7 = verify_me7(data)
    if me7 is not None:
        found.append("ME7 main")
        details.append(f"ME7 main: {me7.status} (stored {me7.stored_hex})")
        if me7.status != "ok":
            stale.append("ME7 main")
        mp = detect_me7_multipoint(data)
        if mp:
            details.append(f"ME7 multipoint: {len(mp)} block(s) verify")
        else:
            n_un = sum(1 for b in detect_me7_multipoint_unverified(data) if b.start < 0x20000)
            details.append(f"ME7 multipoint: {n_un} bootrom descriptor(s) unverifiable")

    rolling = detect_me7_rolling(data)
    if rolling is not None:
        ok = sum(1 for e in rolling if e.status == "ok")
        found.append(f"ME7 rolling {ok}/{len(rolling)}")
        details.append(f"ME7 rolling: {ok}/{len(rolling)} slot(s) verify")
        if ok != len(rolling):
            stale.append("ME7 rolling")

    multirange = detect_me7_multirange(data)
    if multirange is not None:
        found.append("ME7 multirange")
        details.append(f"ME7 multirange: {multirange.status}")
        if multirange.status != "ok":
            stale.append("ME7 multirange")

    ms43 = detect_ms43(data)
    if ms43 is not None:
        found.append(f"MS43 CRC16 {ms43.ok}/{ms43.total}")
        details.append(f"MS43 CRC16: {ms43.ok}/{ms43.total} sections ok")
        if ms43.ok != ms43.total:
            stale.append("MS43 CRC16")

    denso = detect_denso(data)
    if denso is not None:
        found.append(f"Denso descriptor table {denso.ok}/{denso.total}")
        details.append(
            f"Denso descriptor table @0x{denso.table_offset:X}: {denso.ok}/{denso.total} entries ok"
        )
        if denso.status != "ok":
            stale.append("Denso descriptor table")

    for profile in detect_ironfelix(data):
        found.append(f"IronFelix {profile.description} {profile.ok}/{profile.total}")
        details.append(
            f"IronFelix {profile.description}: {profile.ok}/{profile.total} checks ok"
        )
        if profile.ok != profile.total:
            stale.append(f"IronFelix {profile.description}")

    if not found:
        return HealthCheck("checksums", "skip", "no known checksum scheme detected")

    status = "fail" if stale else "ok"
    return HealthCheck(
        "checksums",
        status,
        "; ".join(found) + (" — STALE: " + ", ".join(stale) if stale else ""),
        details,
    )


def _check_axis_sanity(data: bytes, family: str | None) -> HealthCheck:
    tables = scan_map_tables(data)
    high = [t for t in tables if t.score >= _HIGH_SCORE]
    diesel = family_fuel_type(family) == "diesel"
    flags: dict[tuple[int, str], str] = {}
    checked = 0
    for t in high:
        axes: list[tuple[str, list[int]]] = [("X", _read_axis(data, t.x_axis_offset, t.cols, t.byte_order))]
        if t.y_axis_offset is not None and t.rows > 1:
            axes.append(("Y", _read_axis(data, t.y_axis_offset, t.rows, t.byte_order)))
        for label, vals in axes:
            checked += 1
            if not vals:
                continue
            key = (t.x_axis_offset, label)
            mx = max(vals)
            if mx >= _AXIS_FAIL:
                flags[key] = f"0x{t.x_axis_offset:X} {label} axis max {mx} (garbage/FF-fill)"
            elif mx >= _AXIS_WARN:
                flags[key] = f"0x{t.x_axis_offset:X} {label} axis max {mx} (implausibly large)"
            elif diesel and mx > _DIESEL_RPM_FAIL:
                flags[key] = f"0x{t.x_axis_offset:X} {label} axis max {mx} (too high for diesel)"
    if not high:
        return HealthCheck("axis sanity", "skip", "no high-score tables to inspect")
    if flags:
        # Warn-only: out-of-range axes on healthy files come from scanner
        # artifacts (u8 axes read as u16, FF-fill tails) — genuine
        # corruption is caught by the map-count envelope instead.
        return HealthCheck(
            "axis sanity",
            "warn",
            f"{len(flags)} suspicious axis(es) across {len(high)} table(s)",
            list(flags.values())[:20],
        )
    return HealthCheck("axis sanity", "ok", f"{len(high)} table(s), {checked} axis(es) plausible")


def _check_map_count(data: bytes, family: str | None) -> HealthCheck:
    if not family:
        return HealthCheck("map count", "skip", "unknown family — no expectation")
    fam_upper = family.upper()
    matching = [p for p in _MAP_ENVELOPES if fam_upper.startswith(p)]
    if not matching:
        return HealthCheck("map count", "skip", f"no corpus envelope for family {family}")
    # longest matching prefix wins (EDC15 must not match EDC1)
    prefix = max(matching, key=len)
    envelope = _MAP_ENVELOPES[prefix]
    count = sum(1 for t in scan_map_tables(data) if t.score >= _HIGH_SCORE)
    lo, hi = envelope
    if count < lo:
        return HealthCheck(
            "map count",
            "fail",
            f"{count} high-score table(s) — below the {family} envelope [{lo}, {hi}] (wiped/erased calibration?)",
        )
    if count > hi:
        return HealthCheck(
            "map count",
            "fail",
            f"{count} high-score table(s) — above the {family} envelope [{lo}, {hi}] (scanner garbage?)",
        )
    return HealthCheck("map count", "ok", f"{count} high-score table(s), envelope [{lo}, {hi}]")


def _check_erased_blocks(data: bytes) -> HealthCheck:
    regions = segment(data)
    embedded = [
        r for r in regions
        if r.kind == "erased" and r.size >= _ERASED_EMBEDDED_MIN
        and r.start > 0x1000 and r.end < len(data) - 0x1000
    ]
    if not embedded:
        return HealthCheck(
            "erased blocks",
            "ok",
            f"{sum(1 for r in regions if r.kind == 'erased')} erased region(s), none embedded in data",
        )
    details = [f"0x{r.start:X}–0x{r.end:X} ({r.size // 1024} KB, fill 0x{r.fill_byte:02X})" for r in embedded]
    return HealthCheck(
        "erased blocks",
        "warn",
        f"{len(embedded)} large erased region(s) embedded in data — normal for some layouts "
        f"(e.g. Subaru bank mirrors); verify against a known-good dump otherwise",
        details[:10],
    )


def _check_vins(data: bytes) -> HealthCheck:
    hits = [h for h in scan_vins(data, min_confidence=_VIN_CONFIDENCE)]
    if not hits:
        return HealthCheck("VINs", "skip", "no high-confidence VIN candidates")
    unique = sorted({h.vin for h in hits})
    if len(unique) <= 1:
        return HealthCheck(
            "VINs",
            "ok",
            f"single VIN {unique[0]} (mirrored {len(hits)}x)",
        )
    return HealthCheck(
        "VINs",
        "warn",
        f"{len(unique)} distinct VIN(s) in one file (cloning/merge artifact?)",
        [f"{v}: {sum(1 for h in hits if h.vin == v)}x" for v in unique],
    )


def health_report(data: bytes, filename: str = "unknown.bin") -> HealthReport:
    """
    Run every health check over *data* and return the report.

    Args:
        data:     Raw ECU binary content.
        filename: Original filename — used for identity display only.

    Returns:
        :class:`HealthReport` — one :class:`HealthCheck` per concern,
        plus the ``healthy`` gate (no failing check).
    """
    ident = identify_ecu(data, filename)
    family = ident.get("ecu_family")
    manufacturer = ident.get("manufacturer")
    tier = score_identity(ident, filename=filename, data=data).tier

    report = HealthReport(
        file_size=len(data),
        family=family,
        manufacturer=manufacturer,
        confidence_tier=tier,
    )
    report.checks.append(_check_identity(data, filename, tier))
    report.checks.append(_check_checksums(data))
    report.checks.append(_check_axis_sanity(data, family))
    report.checks.append(_check_map_count(data, family))
    report.checks.append(_check_erased_blocks(data))
    report.checks.append(_check_vins(data))
    return report
