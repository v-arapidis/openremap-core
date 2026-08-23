"""
Tests for DensoSH7058Extractor (512 KB Subaru application).

Covers:
  - Identity properties: name ("Denso"), supported_families ("SH7058")
  - can_handle():
      * True  — synthetic 512 KB bins with either descriptor variant,
                CAL ID, and "Copr.DENSO" anchor
      * False — wrong sizes, bad descriptor, bad CAL ID shape, missing
                DENSO anchor
  - extract():
      * required fields always present
      * software_version == CAL ID at 0x2000
      * calibration_id == internal ID at 0x2009
      * match_key == "SH7058::<CAL>"
  - Corpus (skip-guarded): every 512 KB file in tests/data/ECUs/Subaru
    is claimed by this extractor and yields a non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.denso.sh7058 import (
    DensoSH7058Extractor,
    VALID_MARKERS,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

EXTRACTOR = DensoSH7058Extractor()

SIZE = 0x80000


def build_sh7058(
    marker: bytes = b"\x31\x91\x00\x05",
    cal: bytes = b"A2WC400H",
    internal: bytes = b"86CAU_AT",
) -> bytes:
    """Build a valid Denso SH7058-style binary."""
    buf = bytearray([0xFF] * SIZE)
    buf[0x1FFC:0x2000] = marker
    buf[0x2000 : 0x2000 + len(cal)] = cal
    buf[0x2009 : 0x2009 + len(internal)] = internal
    buf[0x2023:0x2031] = b"Copr.DENSO2005"
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_denso(self):
        assert EXTRACTOR.name == "Denso"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["SH7058"]

    def test_repr_contains_manufacturer(self):
        assert "Denso" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_main_marker(self):
        assert EXTRACTOR.can_handle(build_sh7058())

    @pytest.mark.parametrize("marker", VALID_MARKERS)
    def test_all_markers(self, marker):
        assert EXTRACTOR.can_handle(build_sh7058(marker=marker))

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_sh7058())
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
        for size in (0x40000, 0x60000, 0x7FFFF, 0x80001, 0x100000, 0x180000):
            assert not EXTRACTOR.can_handle(b"\xff" * size)

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_bad_marker(self):
        assert not EXTRACTOR.can_handle(build_sh7058(marker=b"\x31\x91\x00\x06"))

    def test_bad_cal_shape(self):
        assert not EXTRACTOR.can_handle(build_sh7058(cal=b"12345678"))

    def test_missing_deniso(self):
        data = bytearray(build_sh7058())
        data[0x2023:0x2031] = b"\x00" * 0xE
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_sh7058())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_sh7058()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_software_version(self):
        assert self._result()["software_version"] == "A2WC400H"

    def test_calibration_id(self):
        assert self._result()["calibration_id"] == "86CAU_AT"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "SH7058"

    def test_match_key(self):
        assert self._result()["match_key"] == "SH7058::A2WC400H"

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
            if Path(f).stat().st_size == SIZE
        ],
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Denso"
        assert r["ecu_family"] == "SH7058"
        assert r["match_key"] == f"SH7058::{r['software_version'].upper()}"
