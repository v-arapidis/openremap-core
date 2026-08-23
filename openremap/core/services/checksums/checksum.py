"""
Checksum sweep + verification — detect which checksum schemes a binary
satisfies, and verify whether a known scheme is OK or STALE.

Design (honest by construction):
- The scheme space is CLOSED and parameterized: 11 algorithm families
  (byte/word sums, XORs, CRC-8/16-CCITT/16-ARC/32-IEEE) × init values ×
  final XOR × regions (whole-file, 16/32/64 KB pages, tail exclusions)
  × store locations (file end, page end) × direct / two's-complement.
- ``sweep()`` tests every combination at native speed (Rust) and reports
  which ones verify.  A single random match is noise (~1/256 .. 1/2^32
  per store); a per-page scheme matching >= 90% of NON-ERASED pages is
  a real signal; family consensus across many factory files is proof
  (ISSUE-3 — the freely-downloadable corpus lacks
  clean factory files, so the family registry is built incrementally).
- ``verify()`` re-checks one known scheme and reports OK / STALE /
  NOT_FOUND.  Verification is detect-only — no correction.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openremap._rust import checksum_compute

# ---------------------------------------------------------------------------
# Scheme vocabulary (mirrors the Rust algorithm ids)
# ---------------------------------------------------------------------------

ALGOS: dict[int, str] = {
    0: "sum8",
    1: "sum16",
    2: "sum16le",
    3: "sum16be",
    4: "xor8",
    5: "xor16le",
    6: "xor16be",
    7: "crc8",
    8: "crc16ccitt",
    9: "crc16arc",
    10: "crc32ieee",
}
_ALGO_BY_NAME = {v: k for k, v in ALGOS.items()}
WIDTH: dict[int, int] = {0: 1, 1: 2, 2: 2, 3: 2, 4: 1, 5: 2, 6: 2, 7: 1, 8: 2, 9: 2, 10: 4}

_ERASED_RATIO = 0.9  # pages with >= 90% single-byte fill carry no checksum
_PAGE_RATE = 0.9  # a page scheme must match >= 90% of non-erased pages
_PAGE_SIZES = (0x4000, 0x8000, 0x10000)  # 16/32/64 KB

_U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class ChecksumScheme:
    """One concrete checksum recipe."""

    algo: str  # one of ALGOS.values()
    init: int
    final_xor: int
    region: str  # "whole" | "page16" | "page32" | "page64"
    exclude_tail: int  # bytes excluded from the region end (0/1/2/4)
    store: str  # "file_end" | "page_end"
    store_le: bool
    complement: bool  # stored value = two's complement of computed

    @property
    def width(self) -> int:
        return WIDTH[_ALGO_BY_NAME[self.algo]]

    @property
    def label(self) -> str:
        tail = f", excludes last {self.exclude_tail} B" if self.exclude_tail else ""
        return (
            f"{self.algo} init=0x{self.init:X} xor=0x{self.final_xor:X} "
            f"{self.region}{tail} → {self.store} "
            f"({'complement' if self.complement else 'direct'}, "
            f"{'LE' if self.store_le else 'BE'})"
        )


@dataclass(frozen=True)
class SchemeMatch:
    """A scheme that verifies, with per-page statistics."""

    scheme: ChecksumScheme
    pages_matched: int
    pages_total: int  # non-erased pages considered

    @property
    def rate(self) -> float:
        return self.pages_matched / self.pages_total if self.pages_total else 1.0


@dataclass(frozen=True)
class Me7Checksum:
    """Located Bosch ME7 main-checksum structure.

    Algorithm (from the open-source ME7Sum project, community knowledge):
    sum of LE u16 words accumulated into a u32 over two descriptor-defined
    blocks, stored as a (value, ~value) u32 pair at file_end - 0x20.
    """

    descriptor_offset: int
    block0: tuple[int, int]  # inclusive byte offsets (descriptor words - base)
    block1: tuple[int, int]
    stored_offset: int


@dataclass(frozen=True)
class ChecksumVerdict:
    """verify() result for one scheme."""

    scheme: ChecksumScheme
    status: str  # "ok" | "stale" | "not_found"
    stored_hex: str
    expected_hex: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_values(algo: str) -> list[int]:
    w = WIDTH[_ALGO_BY_NAME[algo]]
    if w == 1:
        return [0x00, 0xFF]
    if w == 2:
        return [0x0000, 0xFFFF]
    return [0x00000000, 0xFFFFFFFF]


def _xor_values(algo: str) -> list[int]:
    w = WIDTH[_ALGO_BY_NAME[algo]]
    if w == 1:
        return [0x00, 0xFF]
    if w == 2:
        return [0x0000, 0xFFFF]
    return [0x00000000, 0xFFFFFFFF]


def _mask(algo: str) -> int:
    return (1 << (8 * WIDTH[_ALGO_BY_NAME[algo]])) - 1


def _stored(data: bytes, off: int, size: int, le: bool) -> int | None:
    if off < 0 or off + size > len(data):
        return None
    return int.from_bytes(data[off : off + size], "little" if le else "big")


def _is_erased(chunk: bytes) -> bool:
    if not chunk:
        return True
    best = max(chunk.count(0x00), chunk.count(0xFF), chunk.count(0xC3))
    return best / len(chunk) >= _ERASED_RATIO


# ---------------------------------------------------------------------------
# Bosch ME7 main checksum (family scheme — validated on the ME7Sum corpus,
# 83/84 agreement with the reference checker)
# ---------------------------------------------------------------------------

_ME7_BASE = 0x800000


def detect_me7(data: bytes) -> Me7Checksum | None:
    """Locate the ME7 main-checksum descriptor and storage offset.

    The descriptor is a run of 4 u32 LE words [0x800000, 0x800FBFF,
    0x820000, 0x87FFFF|0x8FFFFF] scanned at 2-aligned offsets.  Returns
    None when no descriptor or no valid (v, ~v) pair exists.
    """
    if len(data) < 0x30:
        return None
    stored_offset = len(data) - 0x20
    stored, iv = struct.unpack_from("<2I", data, stored_offset)
    if stored != (~iv & 0xFFFFFFFF):
        return None
    for i in range(0, len(data) - 16, 2):
        v = struct.unpack_from("<4I", data, i)
        if (
            v[0] == _ME7_BASE
            and v[1] == _ME7_BASE + 0xFBFF
            and v[2] == _ME7_BASE + 0x20000
            and v[3] in (_ME7_BASE + 0x7FFFF, _ME7_BASE + 0xFFFFF)
        ):
            return Me7Checksum(
                descriptor_offset=i,
                block0=(v[0] - _ME7_BASE, v[1] - _ME7_BASE),
                block1=(v[2] - _ME7_BASE, v[3] - _ME7_BASE),
                stored_offset=stored_offset,
            )
    return None


def verify_me7(data: bytes, me7: Me7Checksum | None = None) -> ChecksumVerdict | None:
    """Verify the ME7 main checksum.  Returns None when the structure is
    absent (not an ME7-style file); a verdict otherwise ("ok"/"stale")."""
    if me7 is None:
        me7 = detect_me7(data)
    if me7 is None:
        return None

    c0 = checksum_compute(
        data, [(11, 0, me7.block0[0], me7.block0[1] + 1)]
    )[0]
    c1 = checksum_compute(
        data, [(11, 0, me7.block1[0], me7.block1[1] + 1)]
    )[0]
    total = (c0 + c1) & 0xFFFFFFFF

    stored, iv = struct.unpack_from("<2I", data, me7.stored_offset)
    stored_hex = f"{stored:08X}"
    expected_hex = f"{total:08X}"
    scheme = ChecksumScheme(
        algo="me7_main", init=0, final_xor=0, region="descriptor_blocks",
        exclude_tail=0, store="file_end-0x20", store_le=True,
        complement=bool(stored == (~iv & 0xFFFFFFFF)),
    )
    status = "ok" if total == stored else "stale"
    return ChecksumVerdict(
        scheme=scheme, status=status,
        stored_hex=stored_hex, expected_hex=expected_hex,
    )


# ---------------------------------------------------------------------------
# Bosch ME7 multipoint checksums (per-block descriptors, chain-scanned)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Me7MultipointBlock:
    """One multipoint checksum block: 16-byte descriptor
    (start, end, checksum, ~checksum) with a valid, verifying sum."""

    offset: int  # descriptor offset in the file
    start: int  # summed range start (file offset)
    end: int  # summed range end, exclusive (file offset)
    stored: int  # stored checksum value


def detect_me7_multipoint(
    data: bytes, base: int = _ME7_BASE, max_blocks: int = 128
) -> list[Me7MultipointBlock]:
    """Locate all valid multipoint checksum blocks.

    A block is a 16-byte descriptor (4 u32 LE: start, end, checksum,
    ~checksum) at a 2-aligned offset whose sum of LE u16 words (u32
    accumulator) over [start-base, end-base) equals the stored value.
    Self-validating — no whitelist: the pair constraint (2^-32) plus the
    checksum constraint (2^-32) makes false positives essentially zero.
    """
    from openremap._rust import me7_multipoint_scan

    blocks: list[Me7MultipointBlock] = []
    for offset, start, end, cks, valid in me7_multipoint_scan(data, base):
        if valid:
            blocks.append(
                Me7MultipointBlock(offset, start - base, end - base, cks)
            )
            if len(blocks) >= max_blocks:
                break
    return blocks


def detect_me7_multipoint_unverified(
    data: bytes, base: int = _ME7_BASE, max_blocks: int = 128
) -> list[Me7MultipointBlock]:
    """Blocks with a valid (v, ~v) pair whose checksum does not verify
    from the file — bootrom-region descriptors (the bootrom is not part
    of the flash dump; community tools whitelist them, e.g. 0x0FA0F5CF /
    0x0F4716B3), descriptor tables whose regions include non-dumped
    bootrom content, and genuinely stale blocks in uncorrected tunes.
    Advisory only — verified blocks are the real signal."""
    from openremap._rust import me7_multipoint_scan

    out: list[Me7MultipointBlock] = []
    for offset, start, end, cks, valid in me7_multipoint_scan(data, base):
        if not valid:
            out.append(Me7MultipointBlock(offset, start - base, end - base, cks))
            if len(out) >= max_blocks:
                break
    return out


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def sweep(data: bytes) -> list[SchemeMatch]:
    """
    Test every scheme in the closed config space against *data*.

    Returns matches sorted by page rate (descending).  Whole-file schemes
    report pages_total=1.  Erased pages are excluded from page statistics
    (they cannot carry checksums).
    """
    n = len(data)
    matches: list[SchemeMatch] = []
    per: dict[ChecksumScheme, list[int]] = {}  # scheme -> [matched, total]

    # Build jobs: (algo, init, region_start, region_end) — one per
    # (algo, init, region) so the expensive pass runs once per combo.
    jobs: list[tuple[int, int, int, int]] = []
    meta: list[tuple[str, int, str, int, int]] = []  # (algo, init, region, s, e)
    page_totals: dict[int, int] = {}
    for ps in _PAGE_SIZES:
        if n < 2 * ps:
            continue
        page_totals[ps] = sum(
            1
            for s in range(0, n - n % ps, ps)
            if not _is_erased(data[s : min(s + ps, n)])
        )
    for algo_name in ALGOS.values():
        aid = _ALGO_BY_NAME[algo_name]
        w = WIDTH[aid]
        for iv in _init_values(algo_name):
            # whole-file variants
            for excl in (0, 1, 2, 4):
                if excl >= w:
                    jobs.append((aid, iv, 0, n - excl))
                    meta.append((algo_name, iv, "whole", 0, n - excl))
            # per-page variants (region = page minus the stored tail)
            for ps in _PAGE_SIZES:
                if n < 2 * ps:
                    continue
                for s in range(0, n - n % ps, ps):
                    e = min(s + ps, n)
                    if _is_erased(data[s:e]):
                        continue
                    jobs.append((aid, iv, s, e - w))
                    meta.append((algo_name, iv, f"page{ps // 1024}", s, e))

    vals = checksum_compute(data, jobs)

    for (algo_name, iv, region, s, e), val in zip(meta, vals):
        if val == _U64_MAX:
            continue
        w = WIDTH[_ALGO_BY_NAME[algo_name]]
        mask = _mask(algo_name)
        for xv in _xor_values(algo_name):
            exp = val ^ xv
            compl = (mask + 1 - (exp & mask)) & mask
            if region == "whole":
                store_off = len(data) - w
                for le in (True, False):
                    stored = _stored(data, store_off, w, le)
                    if stored is None:
                        continue
                    for use_compl, expected in ((False, exp & mask), (True, compl)):
                        if stored == expected:
                            scheme = ChecksumScheme(
                                algo=algo_name, init=iv, final_xor=xv,
                                region="whole", exclude_tail=n - e,
                                store="file_end", store_le=le,
                                complement=use_compl,
                            )
                            if scheme not in per:
                                per[scheme] = [0, 1]
                            per[scheme][0] = 1
            else:
                # This job covers ONE page [s, e); compare that page's own
                # stored value.  Aggregation happens across jobs via the
                # scheme key.
                ps = int(region[4:]) * 1024
                for le in (True, False):
                    stored = _stored(data, e - w, w, le)
                    if stored is None:
                        continue
                    for use_compl, expected in ((False, exp & mask), (True, compl)):
                        if stored == expected:
                            scheme = ChecksumScheme(
                                algo=algo_name, init=iv, final_xor=xv,
                                region=region, exclude_tail=w,
                                store="page_end", store_le=le,
                                complement=use_compl,
                            )
                            if scheme not in per:
                                per[scheme] = [0, page_totals.get(ps, 0)]
                            per[scheme][0] += 1

    for scheme, (m, t) in per.items():
        if scheme.region == "whole" or (t >= 3 and m / t >= _PAGE_RATE):
            matches.append(
                SchemeMatch(scheme=scheme, pages_matched=m, pages_total=t)
            )
    matches.sort(key=lambda x: -x.rate)
    return matches


# ---------------------------------------------------------------------------
# Verify one known scheme
# ---------------------------------------------------------------------------


def verify(data: bytes, scheme: ChecksumScheme) -> ChecksumVerdict:
    """Recompute *scheme* for *data* and compare against the stored value.

    status: "ok" when the stored value(s) equal the expected one(s),
    "stale" when the store location exists but holds a different value,
    "not_found" when the store location is out of range.
    For page schemes every non-erased page is checked; any mismatch →
    "stale".
    """
    aid = _ALGO_BY_NAME[scheme.algo]
    w = WIDTH[aid]
    n = len(data)
    mask = _mask(scheme.algo)

    regions: list[tuple[int, int]] = []
    if scheme.region == "whole":
        regions.append((0, n - scheme.exclude_tail))
    else:
        ps = int(scheme.region[4:]) * 1024
        regions = [
            (s, min(s + ps, n) - scheme.exclude_tail)
            for s in range(0, n - n % ps, ps)
        ]

    vals = checksum_compute(
        data, [(aid, scheme.init, s, e) for s, e in regions]
    )

    stored_hex = ""
    expected_hex = ""
    any_seen = False
    for (s, e), val in zip(regions, vals):
        exp = val ^ scheme.final_xor
        if scheme.complement:
            expected = (mask + 1 - (exp & mask)) & mask
        else:
            expected = exp & mask

        if scheme.store == "file_end":
            store_off = n - w
            chunk = data[:]
            active = True
        else:
            ps = int(scheme.region[4:]) * 1024
            page_end = min(s + ps, n)
            if _is_erased(data[s:page_end]):
                continue
            store_off = page_end - w
            active = True

        stored = _stored(data, store_off, w, scheme.store_le)
        if stored is None:
            return ChecksumVerdict(
                scheme=scheme, status="not_found",
                stored_hex="", expected_hex=f"{expected:0{w*2}X}",
            )
        any_seen = True
        if not stored_hex:
            stored_hex = f"{stored:0{w*2}X}"
            expected_hex = f"{expected:0{w*2}X}"
        if stored != expected:
            return ChecksumVerdict(
                scheme=scheme, status="stale",
                stored_hex=stored_hex, expected_hex=expected_hex,
            )

    if not any_seen:
        return ChecksumVerdict(
            scheme=scheme, status="not_found",
            stored_hex="", expected_hex="",
        )
    return ChecksumVerdict(
        scheme=scheme, status="ok",
        stored_hex=stored_hex, expected_hex=expected_hex,
    )
