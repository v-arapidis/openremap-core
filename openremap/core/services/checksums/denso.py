"""
Denso Subaru checksums — SH72531 1 MB petrol + EE20 diesel.

Ground truth: the RomRaider source (GPL) `RomChecksum.java` documents the
descriptor format for Subaru's EcuFlash "subarudbw" checksum tables, and
EcuFlash defs (td-d/SubaruDefs) give table locations per CAL ID
(0xFFB80 for the 1 MB SH72531 family, 0x7FB80 for some 512 KB SH7058,
0x13F500/0x13FEFC for 2 MB SH72546).  Corpus verification showed the
factory files verify with ONE refinement over the RomRaider port:

    The descriptor ``end`` address is INCLUSIVE of the last byte.

i.e. the sum runs over BE32 words covering bytes ``[start, end+1)``:

    sum    = Σ u32be(data[i]) for i in range(start, end+1, 4)
    valid  ⇔  (sum + diff) & 0xFFFFFFFF == 0x5AA5A55A

(RomRaider's Java port sums ``[start, end)`` and would report the factory
files stale by the value of the trailing word — the community's EcuFlash
DLL computes with the inclusive end, and the factory ROMs agree.)

Each entry is 12 bytes: ``[start u32be][end u32be][diff u32be]``.
``start == 0 and end == 0`` marks a disabled entry.  The table lives at a
family-known address but is also structurally discoverable: consecutive
entries with aligned, in-file regions that verify.

Closed findings (2026-08-15):
- 16-bit SH7055 ROMs: no stored checksum — a full single-byte mutation
  probe of a factory 192 KB file changes no other byte.
- 512 KB SH7058 ROMs in the corpus carry no table (defs place one only
  for specific CAL IDs, e.g. E6PF101A @0x7FB80 — file absent from corpus).
- 2 MB SH72546: tables exist in defs at per-ROM addresses but our two
  corpus files show no verifying table (their def addresses differ) —
  open question.

The table scan is O(filesize) thanks to prefix sums; ~1 s per 1 MB in
pure Python — a Rust migration candidate (see
docs/rust-migration-audit.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openremap._rust import detect_denso as _rust_detect_denso  # type: ignore[import-untyped]

#: Sum target — the "5A A5 A5 5A" pattern seen across all community tools.
CHECK_TOTAL = 0x5AA5A55A

#: Entry size in bytes (start, end, diff — three BE32 words).
_ENTRY_SIZE = 12

#: Minimum consecutive verifying entries for a table to be trusted.
_MIN_ENTRIES = 3

#: Maximum table length scanned (entries beyond this are not examined).
_MAX_ENTRIES = 32


@dataclass(frozen=True)
class DensoChecksumEntry:
    """One descriptor entry with its verification verdict."""

    index: int
    start: int
    end: int  # descriptor end — INCLUSIVE of the last covered byte
    stored: int | None  # the diff field; None for disabled entries
    expected: int | None
    status: str  # "ok" | "stale" | "disabled" | "bad_range"


@dataclass
class DensoChecksumInfo:
    """Result of detecting the Denso Subaru checksum table."""

    table_offset: int
    entries: list[DensoChecksumEntry] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return sum(1 for e in self.entries if e.status == "ok")

    @property
    def total(self) -> int:
        return sum(1 for e in self.entries if e.status != "disabled")

    @property
    def status(self) -> str:
        """Overall verdict: ok / stale / bad (no verifiable entries)."""
        verified = [e for e in self.entries if e.status != "disabled"]
        if not verified:
            return "bad"
        return "ok" if all(e.status == "ok" for e in verified) else "stale"


def detect_denso(data: bytes) -> DensoChecksumInfo | None:
    """
    Find and verify the Denso Subaru checksum descriptor table.

    The table scan (prefix sums, structural filter, run walk) runs
    natively in Rust (`_rs/src/checksums/denso.rs`) — ~110x faster than
    the original Python byte scan (10 ms vs 1.1 s per 1 MB); parity
    verified on 191 real files + flip-stale cases (2026-08-15, see
    docs/rust-migration-audit.md).  This wrapper only builds the
    ``DensoChecksumInfo`` dataclass from the Rust result.

    Args:
        data: Raw ECU binary.

    Returns:
        :class:`DensoChecksumInfo` with per-entry verdicts, or None.
    """
    if len(data) < 0x4000:
        return None

    raw = _rust_detect_denso(data)
    if raw is None:
        return None

    table_offset, raw_entries = raw
    entries: list[DensoChecksumEntry] = []
    for k, start, end, stored, expected, status in raw_entries:
        if status == "disabled":
            entries.append(DensoChecksumEntry(k, 0, 0, None, None, "disabled"))
        else:
            entries.append(
                DensoChecksumEntry(
                    k, start, end, stored, expected, status
                )
            )
    return DensoChecksumInfo(table_offset=table_offset, entries=entries)
