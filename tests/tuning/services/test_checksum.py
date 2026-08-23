"""Tests for openremap.core.services.checksum — synthetic ground truth.

Every injected scheme is computed with an INDEPENDENT implementation
(plain Python loops / zlib) so the tests are not circular: the engine
must find exactly what we injected, report OK on it, and STALE after a
single data-byte corruption.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path

from openremap._rust import checksum_compute
from openremap.core.services.checksums.checksum import (
    ChecksumScheme,
    sweep,
    verify,
)


# ---------------------------------------------------------------------------
# Independent ground-truth math
# ---------------------------------------------------------------------------


def _sum16(data: bytes, init: int = 0) -> int:
    s = init & 0xFFFF
    for b in data:
        s = (s + b) & 0xFFFF
    return s


def _sum8(data: bytes, init: int = 0) -> int:
    s = init & 0xFF
    for b in data:
        s = (s + b) & 0xFF
    return s


class TestEngine:
    def test_crc32ieee_matches_zlib(self):
        data = os.urandom(4096)
        (mine,) = checksum_compute(
            data, [(10, 0xFFFFFFFF, 0, len(data))]  # crc32ieee, init FF
        )
        # zlib applies a final XOR 0xFFFFFFFF; the engine returns the
        # raw register value.
        expected = zlib.crc32(data) ^ 0xFFFFFFFF
        assert mine == expected

    def test_sum16_matches_python(self):
        data = os.urandom(1024)
        (mine,) = checksum_compute(data, [(1, 0, 0, len(data))])
        assert mine == _sum16(data)

    def test_region_bounds_respected(self):
        data = os.urandom(1024)
        (mine,) = checksum_compute(data, [(1, 0, 16, 512)])
        assert mine == _sum16(data[16:512])


class TestSweepGroundTruth:
    def test_whole_file_sum16_complement_found(self):
        payload = os.urandom(0x10000)
        s = _sum16(payload[:-2])
        stored = (0x10000 - s) & 0xFFFF
        data = payload[:-2] + stored.to_bytes(2, "little")

        matches = sweep(data)
        hit = [
            m for m in matches
            if m.scheme.algo == "sum16" and m.scheme.complement
        ]
        assert hit, f"injected sum16-complement not found; got {[m.scheme.label for m in matches[:5]]}"

        v = verify(data, hit[0].scheme)
        assert v.status == "ok"

    def test_corruption_turns_ok_to_stale(self):
        payload = os.urandom(0x10000)
        s = _sum16(payload[:-2])
        stored = (0x10000 - s) & 0xFFFF
        data = bytearray(payload[:-2] + stored.to_bytes(2, "little"))

        matches = sweep(bytes(data))
        hit = [m for m in matches if m.scheme.algo == "sum16" and m.scheme.complement][0]

        data[100] ^= 0xFF  # one data byte flips — the checksum goes stale
        v = verify(bytes(data), hit.scheme)
        assert v.status == "stale"

    def test_per_page_sum8_complement_found(self):
        # 4 pages x 32 KB, each page = random data + 1-byte complement sum
        pages = []
        for _ in range(4):
            p = bytearray(os.urandom(0x8000))
            s = _sum8(p[:-1])
            p[-1] = (0x100 - s) & 0xFF
            pages.append(bytes(p))
        data = b"".join(pages)

        matches = sweep(data)
        hit = [
            m for m in matches
            if m.scheme.algo == "sum8" and m.scheme.region == "page32"
            and m.scheme.complement and m.rate >= 0.9
        ]
        assert hit, f"page32 sum8-complement not found; got {[m.scheme.label for m in matches[:5]]}"
        assert hit[0].pages_matched == 4

        v = verify(data, hit[0].scheme)
        assert v.status == "ok"

    def test_per_page_corruption_detected(self):
        pages = []
        for _ in range(4):
            p = bytearray(os.urandom(0x8000))
            s = _sum8(p[:-1])
            p[-1] = (0x100 - s) & 0xFF
            pages.append(p)
        data = bytearray(b"".join(bytes(p) for p in pages))

        matches = sweep(bytes(data))
        hit = [m for m in matches if m.scheme.region == "page32" and m.scheme.complement][0]

        data[0x9000] ^= 0xFF  # corrupt page 2's data
        v = verify(bytes(data), hit.scheme)
        assert v.status == "stale"

    def test_no_page_false_positives_on_random(self):
        data = os.urandom(0x40000)
        matches = sweep(data)
        page_matches = [m for m in matches if m.scheme.region.startswith("page")]
        assert page_matches == [], [
            m.scheme.label for m in page_matches[:5]
        ]

    def test_verify_not_found_on_empty(self):
        v = verify(
            b"",
            ChecksumScheme(
                algo="sum16", init=0, final_xor=0, region="whole",
                exclude_tail=2, store="file_end", store_le=True,
                complement=True,
            ),
        )
        assert v.status == "not_found"


# ---------------------------------------------------------------------------
# Bosch ME7 main checksum — corpus-validated family scheme
# ---------------------------------------------------------------------------


class TestMe7Scheme:
    def test_detect_and_verify_on_corpus(self):
        """Reference corpus: the ME7Sum bins (ME7Check labels 80+ OK)."""
        import pytest
        from openremap.core.services.checksums.checksum import detect_me7, verify_me7

        repo = Path(__file__).parent.parent.parent.parent
        me7_dir = repo / "tests" / "data" / "ECUs" / "Bosch" / "ME7.1"
        bins = sorted(me7_dir.glob("*.bin")) if me7_dir.exists() else []
        if not bins:
            pytest.skip("ME7Sum corpus not present")
        assert len(bins) >= 80

        ok = stale = absent = 0
        for p in bins:
            v = verify_me7(p.read_bytes())
            if v is None:
                absent += 1
            elif v.status == "ok":
                ok += 1
            else:
                stale += 1

        # Every bin with a valid checksum pair must verify (factory files);
        # the reference checker labels 80+ OK, 3 fail on OTHER checks
        # (multipoint/CRC — out of scope here), 1 has a blanked pair.
        assert ok >= 80, f"ok={ok} stale={stale} absent={absent}"
        assert absent <= 2

    def test_multipoint_blocks_verify_on_corpus(self):
        import pytest
        from openremap.core.services.checksums.checksum import detect_me7_multipoint

        repo = Path(__file__).parent.parent.parent.parent
        me7_dir = repo / "tests" / "data" / "ECUs" / "Bosch" / "ME7.1"
        bins = sorted(me7_dir.glob("*.bin")) if me7_dir.exists() else []
        if not bins:
            pytest.skip("ME7Sum corpus not present")
        checked = 0
        for p in bins:
            blocks = detect_me7_multipoint(p.read_bytes())
            assert len(blocks) >= 4, f"{p.name}: only {len(blocks)} multipoint blocks"
            checked += 1
        assert checked >= 80

    def test_detect_rejects_non_me7_files(self):
        import os
        from openremap.core.services.checksums.checksum import detect_me7

        assert detect_me7(os.urandom(0x10000)) is None
        assert detect_me7(b"") is None
        assert detect_me7(b"\x00" * 0x20000) is None
