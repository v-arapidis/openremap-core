"""
Tests for DensoDieselExtractor (1 MB EE20 diesel Subaru application).

Covers:
  - Identity properties: name ("Denso"), supported_families ("Diesel")
  - can_handle():
      * True  — synthetic 1 MB bin with K###ZQ2DT### block and
                "Cpyr.DENSO" anchor
      * False — wrong sizes, bad block tag, bad CAL ID shape, missing
                Cpyr.DENSO anchor, standard "Copr.DENSO" (petrol spelling)
  - extract():
      * required fields always present
      * software_version == CAL ID at 0x400C
      * match_key == "DIESEL::<CAL>"
  - Corpus (skip-guarded): every diesel file in tests/data/ECUs/Subaru
    is claimed by this extractor and yields a non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.denso.diesel import (
    CAL_OFFSET,
    DensoDieselExtractor,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

EXTRACTOR = DensoDieselExtractor()

SIZE = 0x100000


def build_diesel(cal: bytes = b"JZ2F401A") -> bytes:
    """Build a valid Denso diesel-style binary."""
    buf = bytearray([0xFF] * SIZE)
    buf[0x4000:0x400C] = b"K321ZQ2DT140"
    buf[CAL_OFFSET : CAL_OFFSET + len(cal)] = cal
    buf[0x4023:0x4031] = b"Cpyr.DENSO2009"
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_denso(self):
        assert EXTRACTOR.name == "Denso"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["Diesel"]

    def test_repr_contains_manufacturer(self):
        assert "Denso" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_valid(self):
        assert EXTRACTOR.can_handle(build_diesel())

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_diesel())
        ev = set(EXTRACTOR.last_detection_evidence)
        assert "SIZE_MATCH" in ev
        assert "MAGIC_MATCH" in ev
        assert "IDENT_BLOCK" in ev
        assert "FAMILY_STRING" in ev


# ---------------------------------------------------------------------------
# can_handle() — negative detection
# ---------------------------------------------------------------------------


class TestCanHandleFalse:
    def test_wrong_sizes(self):
        for size in (0x80000, 0xFFFFF, 0x100001, 0x180000):
            assert not EXTRACTOR.can_handle(b"\xff" * size)

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_bad_block_tag(self):
        data = bytearray(build_diesel())
        data[0x4004:0x4009] = b"ZQ2XX"
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_bad_k_prefix(self):
        data = bytearray(build_diesel())
        data[0x4000] = 0x58  # "X"
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_bad_cal_shape(self):
        assert not EXTRACTOR.can_handle(build_diesel(cal=b"12345678"))

    def test_missing_cpyr_deniso(self):
        data = bytearray(build_diesel())
        data[0x4023:0x4031] = b"\x00" * 0xE
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_petrol_copr_spelling_rejected(self):
        # A petrol-style identity block ("Copr.DENSO") must not pass the
        # diesel detector.
        data = bytearray(build_diesel())
        data[0x4023:0x4031] = b"Copr.DENSO20"
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_diesel())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_diesel()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_software_version(self):
        assert self._result()["software_version"] == "JZ2F401A"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "Diesel"

    def test_match_key(self):
        assert self._result()["match_key"] == "DIESEL::JZ2F401A"

    def test_deterministic(self):
        assert self._result() == self._result()


# ---------------------------------------------------------------------------
# Corpus (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SUBARU, reason="tests/data/ECUs/Subaru corpus missing")
class TestCorpus:
    @pytest.mark.parametrize(
        "path",
        [
            f
            for f in sorted(glob.glob(str(DATA / "ECUs" / "Subaru" / "**" / "*.hex"), recursive=True))
            if "Diesel" in Path(f).name and Path(f).stat().st_size == SIZE
        ],
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Denso"
        assert r["ecu_family"] == "Diesel"
        assert r["match_key"] == f"DIESEL::{r['software_version'].upper()}"
