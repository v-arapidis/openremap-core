"""
Flash-layout segmenter — data-driven block detection for ECU binaries.

ECU flash images are composed of architecture blocks: code, calibration
(map) areas, ident/metadata text blocks, and erased pages.  This module
detects their start/end without any manufacturer database — purely from
byte-level signals, validated against the real binaries in ``tests/data/``.

Signals (measured on the corpus, see tests):
- **Fill** — erased pages are one repeated byte.  The byte varies per
  family/toolchain: ``0xFF`` (most), ``0x00``, even ``0xC3`` (EDC15).
  Dominant-byte ratio >= 0.95 → erased.
- **Map density** — a sector containing at least one table scored >= 0.85
  by the structural scanner is calibration.  Across the corpus, code
  sectors yield exactly zero such tables; calibration sectors yield
  13–48.  (Low-score tables do appear in code — the score threshold is
  what makes this reliable.)
- **Entropy** — high-entropy sectors with no high-score tables are code.

Kinds are **probabilistic labels** (``confidence`` field) — never verified
names.  ``mixed`` is the honest fallback when no signal is decisive.
Bootloader vs program code is deliberately NOT distinguished — both are
``code``; claiming more would require manufacturer knowledge.

Sector granularity: 64 KB for bins >= 256 KB, 16 KB below.  Adjacent
sectors of the same kind are merged into regions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from openremap._rust import (  # type: ignore[import-untyped]
    find_ident_blocks as _rust_find_ident_blocks,
)
from openremap.core.services.entropy import shannon_entropy
from openremap.core.services.maps.map_hunter import MapTable, scan_map_tables

# ---------------------------------------------------------------------------
# Tunables (validated on the tests/data corpus)
# ---------------------------------------------------------------------------

_FILL_ERASED = 0.95  # dominant-byte ratio → erased page
_ENTROPY_CODE = 6.0  # bits/byte → code (when no high-score tables)
_TABLE_SCORE_HIGH = 0.85  # scan score → trustworthy calibration table
_ASCII_RUN_MIN = 64  # bytes of printable text → ident candidate
_SECTOR_BIG = 0x10000  # 64 KB — bins >= 256 KB
_SECTOR_SMALL = 0x4000  # 16 KB — small bins
_BIG_BIN_THRESHOLD = 0x40000  # 256 KB

_REGION_KINDS = ("erased", "code", "calibration", "ident", "mixed")


@dataclass(frozen=True)
class Region:
    """One contiguous block of the flash layout.

    ``start``/``end`` are byte offsets (``end`` exclusive).  ``kind`` is a
    probabilistic label — see ``confidence``.  For ``erased`` regions
    ``fill_byte`` is the repeated byte; ``None`` elsewhere.
    """

    start: int
    end: int
    kind: str
    fill_byte: int | None
    fill_ratio: float
    mean_entropy: float
    tables: int
    tables_high_conf: int
    confidence: float

    @property
    def size(self) -> int:
        return self.end - self.start


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def _sector_size(file_size: int) -> int:
    if file_size <= 0:
        return _SECTOR_SMALL
    if file_size >= _BIG_BIN_THRESHOLD:
        return _SECTOR_BIG
    # Small bins: 16 KB sectors, but never more than 16 sectors so tiny
    # 32 KB files keep 2–4 sectors.
    while file_size // _SECTOR_SMALL > 16:
        return _SECTOR_SMALL * 2
    return _SECTOR_SMALL


def segment(
    data: bytes,
    *,
    sector_size: int | None = None,
    tables: list[MapTable] | None = None,
) -> list[Region]:
    """
    Segment *data* into flash-layout regions.

    Args:
        data:        Raw ECU binary content.
        sector_size: Optional sector granularity override (bytes).
        tables:      Optional precomputed scan result
                     (``scan_map_tables`` output) — pass it to avoid a
                     second scan when the caller already scanned
                     (``attach_maps``, ``diff-maps``).

    Returns:
        Non-overlapping regions sorted by offset.  Kinds: ``erased``,
        ``code``, ``calibration``, ``mixed`` (ident blocks are reported
        by :func:`find_ident_blocks` instead — they are exact byte
        ranges, not sectors).
    """
    if not data:
        return []

    sec = sector_size or _sector_size(len(data))

    if tables is None:
        tables = scan_map_tables(
            data, min_score=0.55, max_series_tables=16,
        )
    # High-score table offsets (the calibration signal).
    high_offsets: set[int] = {
        t.offset for t in tables if t.score >= _TABLE_SCORE_HIGH
    }

    regions: list[Region] = []
    for start in range(0, len(data), sec):
        chunk = data[start : start + sec]
        dom_byte, dom_count = Counter(chunk).most_common(1)[0]
        fill_ratio = dom_count / len(chunk)
        entropy = shannon_entropy(chunk)
        t_hi = sum(
            1 for off in high_offsets if start <= off < start + sec
        )

        if fill_ratio >= _FILL_ERASED:
            kind = "erased"
            confidence = 0.95 if fill_ratio >= 0.99 else 0.85
            fill_byte = dom_byte
        elif t_hi >= 1:
            kind = "calibration"
            confidence = min(0.95, 0.5 + 0.02 * t_hi)
            fill_byte = None
        elif entropy >= _ENTROPY_CODE:
            kind = "code"
            confidence = 0.7
            fill_byte = None
        else:
            kind = "mixed"
            confidence = 0.3
            fill_byte = None

        regions.append(
            Region(
                start=start,
                end=min(start + sec, len(data)),
                kind=kind,
                fill_byte=fill_byte,
                fill_ratio=round(fill_ratio, 4),
                mean_entropy=round(entropy, 2),
                tables=t_hi,
                tables_high_conf=t_hi,
                confidence=confidence,
            )
        )

    # Merge adjacent same-kind sectors.
    merged: list[Region] = []
    for r in regions:
        if merged and merged[-1].kind == r.kind and merged[-1].end == r.start:
            prev = merged[-1]
            total = prev.size + r.size
            weighted_fill = (
                prev.fill_ratio * prev.size + r.fill_ratio * r.size
            ) / total
            weighted_ent = (
                prev.mean_entropy * prev.size + r.mean_entropy * r.size
            ) / total
            fill_byte = (
                prev.fill_byte
                if prev.fill_byte == r.fill_byte
                else None
            )
            merged[-1] = Region(
                start=prev.start,
                end=r.end,
                kind=prev.kind,
                fill_byte=fill_byte,
                fill_ratio=round(weighted_fill, 4),
                mean_entropy=round(weighted_ent, 2),
                tables=prev.tables + r.tables,
                tables_high_conf=prev.tables_high_conf + r.tables_high_conf,
                confidence=min(prev.confidence, r.confidence),
            )
        else:
            merged.append(r)
    return merged


# ---------------------------------------------------------------------------
# Ident blocks
# ---------------------------------------------------------------------------


def find_ident_blocks(data: bytes, *, min_run: int = _ASCII_RUN_MIN) -> list[Region]:
    """
    Locate ident/metadata text blocks — exact byte ranges of readable
    ASCII runs (SW/HW numbers, VINs, cal names live in such blocks).

    Returns ``ident`` regions with exact start/end (they may overlap the
    sector regions returned by :func:`segment`).  Confidence is modest —
    code also contains ASCII strings (compiler banners); the extractor
    layer's ident offsets are the verified refinement.

    The run scan, dominant-byte counts (Counter.most_common(1) semantics)
    and per-block Shannon entropy run natively in Rust
    (`_rs/src/layout_scan.rs`) — ~30x faster than the previous Python
    regex/Counter loop; parity verified on 1,693 corpus files + synthetic
    edge cases (2026-08-15, see docs/rust-migration-audit.md).  This
    function only constructs the ``Region`` dataclass objects and applies
    the same rounding the Python implementation used.
    """
    if not data:
        return []

    return [
        Region(
            start=start,
            end=end,
            kind="ident",
            fill_byte=None,
            fill_ratio=round(dom_count / (end - start), 4),
            mean_entropy=round(entropy, 2),
            tables=0,
            tables_high_conf=0,
            confidence=0.5,
        )
        for (start, end, _dom_byte, dom_count, entropy) in _rust_find_ident_blocks(
            data, min_run
        )
    ]


# ---------------------------------------------------------------------------
# Code regions (feed the arch-domain xref pass — plain (start, end) tuples)
# ---------------------------------------------------------------------------


def code_regions_from_layout(regions) -> list[tuple[int, int]]:
    """``[(start, end)]`` for regions with kind ``"code"``."""
    return [(r.start, r.end) for r in regions if r.kind == "code"]
