"""
Volatile-region classifier — detect recipe instructions that touch
volatile / vehicle-specific bytes and report them with evidence.

A recipe cooked from (stockA, tunedA) fails on stockB of the same SW
revision whenever the tune changed bytes that differ between the two
cars: VIN records, checksum store bytes (tuning tools recompute them on
save), serial/IMMO strings, and low-entropy counter clusters.

This module classifies each instruction into one of two actions:

    excluded — near-certain volatile (VIN, checksum stores).  These can
               be removed from the patch list to make the recipe
               car-portable.
    flagged  — lower-confidence volatile (ident-block strings,
               low-entropy counter/serial regions).  Warnings by
               default; ``exclude_uncertain=True`` promotes them to
               excluded (caller's explicit choice).

Nothing is removed here — the report is pure data.  The CLI
(``cook-volatile``, Phase 2) decides how to act on it.

Honest-by-construction notes
----------------------------

- **VIN exclusion** requires the strongest evidence tier: a known WMI
  *and* candidate confidence >= 0.9 (real-shaped VINs in ident blocks
  score >= 0.9 per :mod:`vin_scanner` corpus measurements; natural
  lookalikes score <= 0.4).
- **Checksum stores** come only from verified family detectors that
  fire on the binary's own structure — never from the closed-config
  sweep (weak evidence).  When no detector fires, no store regions are
  reported and the recipe keeps today's safe behavior (fails on the
  other car instead of guessing).
- Per-instruction precedence: if an instruction has an *excluded*
  finding (VIN or checksum store), the weaker flags are not reported
  for it — the exclusion is the actionable signal.

Deterministic: pure function of (recipe, original_data) — no I/O, no
randomness, no corpus.  Re-cooking the same pair yields the identical
report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from openremap.core.services.checksums.checksum import (
    detect_me7,
    detect_me7_multipoint,
)
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import detect_all as detect_ironfelix
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.checksums.nefmoto import (
    detect_me7_multirange,
    detect_me7_rolling,
)
from openremap.core.services.identify.vin_scanner import scan_vins
from openremap.core.services.maps.layout import find_ident_blocks

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_VIN_CONF_MIN = 0.9  # VIN exclusion tier — real-shaped VINs in ident blocks
_STORE_CONF = 0.95  # verified family detectors only
_IDENT_CONF = 0.5  # ASCII change inside an ident block — plausible serial
_COUNTER_CONF = 0.3  # low-entropy ctx — plausible counter/serial cluster
_LOW_ENTROPY_THRESHOLD = 2.5  # same as annotator.LowEntropyScanner
_ASCII_MIN_RATIO = 0.8  # changed bytes must be mostly printable ASCII
_VIN_CONF_CAP = 0.95

_EXCLUDED = "excluded"
_FLAGGED = "flagged"

# Kind tags (recipe schema 4.5 — volatile-region classification).
KIND_VIN = "VIN"
KIND_CHECKSUM_STORE = "CHECKSUM_STORE"
KIND_SERIAL_OR_IDENT = "SERIAL_OR_IDENT"
KIND_COUNTER_OR_SERIAL = "COUNTER_OR_SERIAL"


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolatileFinding:
    """One classified instruction."""

    index: int  # 0-based index into recipe["instructions"] (pre-exclusion)
    offset: int
    size: int
    kind: str
    confidence: float
    action: str  # "excluded" | "flagged"
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "offset": self.offset,
            "offset_hex": f"{self.offset:X}",
            "size": self.size,
            "kind": self.kind,
            "confidence": self.confidence,
            "action": self.action,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class VolatileReport:
    """Classification result for a whole recipe."""

    excluded: List[VolatileFinding]
    flagged: List[VolatileFinding]

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def bytes_excluded(self) -> int:
        return sum(f.size for f in self.excluded)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "excluded": [f.to_dict() for f in self.excluded],
            "flagged": [f.to_dict() for f in self.flagged],
            "summary": {
                "excluded_count": self.excluded_count,
                "flagged_count": self.flagged_count,
                "bytes_excluded": self.bytes_excluded,
            },
        }


# ---------------------------------------------------------------------------
# Checksum store collection
# ---------------------------------------------------------------------------

# Denso descriptor entries are 12-byte [start][end][diff] BE32 triples;
# the stored diff word sits at entry_offset + 8.
_DENSO_ENTRY_SIZE = 12
_DENSO_DIFF_DELTA = 8

# ME7 multipoint descriptors are 16-byte (start, end, checksum, ~checksum)
# — the store is the trailing 8 bytes.
_MP_STORE_DELTA = 8
_MP_STORE_SIZE = 8

# MS43 stores the CRC16 at the descriptor slot itself (2 bytes).
_MS43_STORE_SIZE = 2

# ME7 main stores a (v, ~v) u32 pair at file_end - 0x20 (8 bytes).
_ME7_MAIN_STORE_SIZE = 8

# NefMoto rolling slots store one u32; multirange stores a (v, ~v) pair.
_ROLLING_STORE_SIZE = 4
_MULTIRANGE_STORE_SIZE = 8


def collect_checksum_stores(
    data: bytes,
) -> Tuple[Tuple[str, int, int], ...]:
    """
    Run every verified family detector against *data* and return the
    checksum store byte ranges as ``(source, start, end)`` tuples
    (``end`` exclusive), deduplicated and sorted by start.

    Sources: "ME7 main", "ME7 multipoint", "NefMoto rolling",
    "NefMoto multirange", "MS43", "Denso", "IronFelix <family>/<check>".
    Detectors fire on the binary's own structure — a range is reported
    only when the detector genuinely validated against this file (no
    cross-family guessing).
    """
    stores: List[Tuple[str, int, int]] = []

    me7 = detect_me7(data)
    if me7 is not None:
        s = me7.stored_offset
        stores.append(("ME7 main", s, s + _ME7_MAIN_STORE_SIZE))

    for block in detect_me7_multipoint(data):
        s = block.offset + _MP_STORE_DELTA
        stores.append(("ME7 multipoint", s, s + _MP_STORE_SIZE))

    for slot in detect_me7_rolling(data) or []:
        stores.append(
            ("NefMoto rolling", slot.store_offset, slot.store_offset + _ROLLING_STORE_SIZE)
        )

    multirange = detect_me7_multirange(data)
    if multirange is not None:
        stores.append(
            (
                "NefMoto multirange",
                multirange.store_offset,
                multirange.store_offset + _MULTIRANGE_STORE_SIZE,
            )
        )

    ms43 = detect_ms43(data)
    if ms43 is not None:
        for check in ms43.crcs:
            stores.append(("MS43", check.slot, check.slot + _MS43_STORE_SIZE))

    denso = detect_denso(data)
    if denso is not None:
        for entry in denso.entries:
            if entry.stored is None:
                continue  # disabled descriptor slot ([0,0,0]) — not a store
            off = (
                denso.table_offset
                + entry.index * _DENSO_ENTRY_SIZE
                + _DENSO_DIFF_DELTA
            )
            stores.append(("Denso", off, off + 4))

    for profile in detect_ironfelix(data):
        for check in profile.checks:
            if check.store_size and check.store_offset is not None:
                s = check.store_offset
                stores.append(
                    (
                        f"IronFelix {profile.family}/{check.name}",
                        s,
                        s + check.store_size,
                    )
                )

    seen: set[Tuple[int, int]] = set()
    unique: List[Tuple[str, int, int]] = []
    for source, start, end in stores:
        key = (start, end)
        if key not in seen:
            seen.add(key)
            unique.append((source, start, end))

    unique.sort(key=lambda t: (t[1], t[2]))
    return tuple(unique)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _ascii_ratio(raw: bytes) -> float:
    if not raw:
        return 0.0
    printable = sum(1 for b in raw if 0x20 <= b <= 0x7E)
    return printable / len(raw)


def _inst_range(inst: Dict[str, Any]) -> Tuple[int, int]:
    start = int(inst["offset"])
    size = inst.get("size")
    if not size:
        # Fallback for recipes missing `size`: derive it from the ob hex
        # length.  Guarded — malformed/odd-length hex degrades to 0 (no
        # overlap reported) instead of crashing.
        try:
            size = len(bytes.fromhex(inst.get("ob", "")))
        except ValueError:
            size = 0
    return start, start + int(size)


def classify_volatile(
    recipe: Dict[str, Any],
    original_data: bytes,
    exclude_uncertain: bool = False,
) -> VolatileReport:
    """
    Classify every instruction of *recipe* against *original_data*.

    Args:
        recipe: Parsed recipe dict (schema >= 4.3) with an
                ``instructions`` list (offset/size/ob/mb/ctx_entropy).
        original_data: The unmodified (stock) binary the recipe was
                       cooked from.
        exclude_uncertain: Promote flagged findings (SERIAL_OR_IDENT,
                           COUNTER_OR_SERIAL) to ``excluded``.

    Returns:
        VolatileReport.  The recipe is NOT modified.
    """
    instructions = recipe.get("instructions", [])
    stores = collect_checksum_stores(original_data)
    vin_hits = [
        h
        for h in scan_vins(original_data, min_confidence=_VIN_CONF_MIN)
        if h.wmi_known
    ]
    ident_blocks = [(r.start, r.end) for r in find_ident_blocks(original_data)]

    excluded: List[VolatileFinding] = []
    flagged: List[VolatileFinding] = []

    for index, inst in enumerate(instructions):
        start, end = _inst_range(inst)
        strong: List[VolatileFinding] = []
        weak: List[VolatileFinding] = []

        for h in vin_hits:
            vin_start = h.offset
            vin_end = h.offset + len(h.vin)
            if _overlaps(start, end, vin_start, vin_end):
                strong.append(
                    VolatileFinding(
                        index=index,
                        offset=start,
                        size=end - start,
                        kind=KIND_VIN,
                        confidence=min(_VIN_CONF_CAP, h.confidence),
                        action=_EXCLUDED,
                        evidence=(
                            f"overlaps VIN-structured record '{h.vin}' "
                            f"at 0x{vin_start:X}-0x{vin_end:X} "
                            f"(confidence {h.confidence}, evidence: "
                            f"{', '.join(h.evidence)})",
                        ),
                    )
                )
                break  # one VIN finding per instruction is enough

        for source, s_start, s_end in stores:
            if _overlaps(start, end, s_start, s_end):
                strong.append(
                    VolatileFinding(
                        index=index,
                        offset=start,
                        size=end - start,
                        kind=KIND_CHECKSUM_STORE,
                        confidence=_STORE_CONF,
                        action=_EXCLUDED,
                        evidence=(
                            f"overlaps {source} checksum store "
                            f"at 0x{s_start:X}-0x{s_end:X}",
                        ),
                    )
                )
                break  # one store finding per instruction is enough

        # Weaker signals are skipped when a strong (excluded) finding
        # exists — the exclusion is the actionable signal.
        if not strong:
            mb_hex = inst.get("mb") or ""
            try:
                mb = bytes.fromhex(mb_hex)
            except ValueError:
                mb = b""

            for block_start, block_end in ident_blocks:
                if _overlaps(start, end, block_start, block_end):
                    ratio = _ascii_ratio(mb)
                    if ratio >= _ASCII_MIN_RATIO:
                        weak.append(
                            VolatileFinding(
                                index=index,
                                offset=start,
                                size=end - start,
                                kind=KIND_SERIAL_OR_IDENT,
                                confidence=_IDENT_CONF,
                                action=_FLAGGED,
                                evidence=(
                                    f"change inside printable-ASCII ident "
                                    f"block 0x{block_start:X}-0x{block_end:X}; "
                                    f"changed bytes are {ratio:.0%} "
                                    f"printable ASCII",
                                ),
                            )
                        )
                    break  # at most one ident-block finding per instruction

            ctx_entropy = inst.get("ctx_entropy")
            if ctx_entropy is not None and ctx_entropy < _LOW_ENTROPY_THRESHOLD:
                weak.append(
                    VolatileFinding(
                        index=index,
                        offset=start,
                        size=end - start,
                        kind=KIND_COUNTER_OR_SERIAL,
                        confidence=_COUNTER_CONF,
                        action=_FLAGGED,
                        evidence=(
                            f"context anchor entropy {ctx_entropy:.1f} "
                            f"bits/byte (below {_LOW_ENTROPY_THRESHOLD}) — "
                            f"repetitive counter/serial region",
                        ),
                    )
                )

        excluded.extend(strong)
        if exclude_uncertain:
            promoted = [
                VolatileFinding(
                    index=f.index,
                    offset=f.offset,
                    size=f.size,
                    kind=f.kind,
                    confidence=f.confidence,
                    action=_EXCLUDED,
                    evidence=f.evidence,
                )
                for f in weak
            ]
            excluded.extend(promoted)
        else:
            flagged.extend(weak)

    return VolatileReport(excluded=excluded, flagged=flagged)
