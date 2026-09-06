"""
Tests for MarelliIAW1APExtractor (ST6-based single-point injection ECU).

Covers:
  - Identity properties: name ("Magneti Marelli"), supported_families (["IAW 1AP"])
  - can_handle():
      * True  — synthetic 64 KB bin with FF header, "1ap" tag at 0x5F8D and
                AA55CC33 sync marker at 0x4810
      * False — wrong size, non-FF header, missing tag, missing marker
  - extract():
      * required fields always present
      * calibration_id == hex of the 4 bytes after the sync marker
      * oem_part_number == "55598889641" (digit run at 0x051C0)
      * match_key == "IAW 1AP::<calibration_id>"
  - Corpus (skip-guarded): the real Peugeot 206 IAW 1AP dump is claimed and
    yields the part number.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.marelli.iaw_1ap.extractor import (
    MarelliIAW1APExtractor,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_IAW1AP = (DATA / "ECUs" / "Unknown" / "Peugeot 206 1.4i 1AP-81__1__1.ori").is_file()

EXTRACTOR = MarelliIAW1APExtractor()

SIZE = 0x10000


def build_iaw1ap(
    size: int = SIZE,
    family_tag: bytes = b"1ap",
    sync_marker: bytes = b"\xaa\x55\xcc\x33",
    part_number: bytes = b"55598889641",
) -> bytes:
    """Build a valid IAW 1AP-style binary."""
    buf = bytearray([0xFF] * size)
    buf[0x5F8D : 0x5F8D + len(family_tag)] = family_tag
    buf[0x4810 : 0x4810 + len(sync_marker)] = sync_marker
    buf[0x4814 : 0x4818] = b"\x50\x96\x06\x54"  # calibration fingerprint
    buf[0x51C0 : 0x51C0 + len(part_number)] = part_number
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_marelli(self):
        assert EXTRACTOR.name == "Magneti Marelli"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["IAW 1AP"]

    def test_repr_contains_manufacturer(self):
        assert "Magneti Marelli" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_valid_binary(self):
        assert EXTRACTOR.can_handle(build_iaw1ap())

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_iaw1ap())
        ev = set(EXTRACTOR.last_detection_evidence)
        assert "SIZE_MATCH" in ev
        assert "HEADER_MATCH" in ev
        assert "EXCLUSION_CLEAR" in ev
        assert "FAMILY_ANCHOR" in ev
        assert "SYNC_MARKER" in ev


# ---------------------------------------------------------------------------
# can_handle() — negative detection
# ---------------------------------------------------------------------------


class TestCanHandleFalse:
    def test_wrong_sizes(self):
        for size in (0, 0xFFFF, 0x10001):
            assert not EXTRACTOR.can_handle(build_iaw1ap(size=size))

    def test_bad_header(self):
        data = bytearray(build_iaw1ap())
        data[0] = 0x00
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_missing_family_tag(self):
        assert not EXTRACTOR.can_handle(build_iaw1ap(family_tag=b"xxx"))

    def test_missing_sync_marker(self):
        assert not EXTRACTOR.can_handle(build_iaw1ap(sync_marker=b"\x00\x00\x00\x00"))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_iaw1ap())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_iaw1ap()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_calibration_id(self):
        assert self._result()["calibration_id"] == "50960654"

    def test_oem_part_number(self):
        assert self._result()["oem_part_number"] == "55598889641"

    def test_match_key(self):
        assert self._result()["match_key"] == "IAW 1AP::50960654"

    def test_deterministic(self):
        assert self._result() == self._result()


# ---------------------------------------------------------------------------
# Corpus (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_IAW1AP, reason="IAW 1AP corpus dump missing")
class TestCorpus:
    def test_corpus_file_claimed_and_extracted(self):
        path = DATA / "ECUs" / "Unknown" / "Peugeot 206 1.4i 1AP-81__1__1.ori"
        data = path.read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["manufacturer"] == "Magneti Marelli"
        assert r["ecu_family"] == "IAW 1AP"
        assert r["oem_part_number"] == "55598889641"
        assert r["calibration_id"] == "50960654"
        assert r["match_key"] == "IAW 1AP::50960654"
