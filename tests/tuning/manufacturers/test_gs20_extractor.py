"""
Tests for SiemensGS20Extractor (BMW automatic-gearbox controller, AGS).

Covers:
  - Identity properties: name ("Siemens"), supported_families (["GS20"])
  - can_handle():
      * True  — synthetic 64 KB bins with the header code "GADC9010" and
                the footer project string "G2210_0090C0AD10"
      * False — wrong sizes, missing header code, missing footer string
  - extract():
      * required fields always present (manufacturer, file_size, md5,
        sha256_first_64kb)
      * software_version == "7557997" (7-digit number at 0x3FD8)
      * calibration_id == "GADC9010" (header code at 0x176)
      * match_key == "GS20::7557997"
      * deterministic; filename does not affect identification fields
  - Corpus (skip-guarded): the 4 real GS20 files are claimed and yield a
    non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.siemens.gs20.extractor import (
    CAL_OFFSET,
    FOOTER_SIGNATURE,
    HEADER_SIGNATURE,
    SiemensGS20Extractor,
    SW_OFFSET,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_GS20 = (DATA / "ECUs" / "Siemens" / "GS20").is_dir()

EXTRACTOR = SiemensGS20Extractor()

SIZE = 0x10000


def build_gs20(
    size: int = SIZE,
    header: bytes | None = b"GADC9010",
    footer: bytes | None = b"G2210_0090C0AD10",
    sw: bytes = b"7557997",
) -> bytes:
    """Build a valid GS20-style binary."""
    buf = bytearray([0xFF] * size)
    if header is not None:
        buf[0x176 : 0x176 + len(header)] = header
    if footer is not None:
        buf[0xFFC8 : 0xFFC8 + len(footer)] = footer
    buf[SW_OFFSET : SW_OFFSET + len(sw)] = sw
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_siemens(self):
        assert EXTRACTOR.name == "Siemens"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["GS20"]

    def test_repr_contains_manufacturer(self):
        assert "Siemens" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_stock_shape(self):
        assert EXTRACTOR.can_handle(build_gs20())

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_gs20())
        ev = set(EXTRACTOR.last_detection_evidence)
        assert "SIZE_MATCH" in ev
        assert "IDENT_BLOCK" in ev
        assert "FAMILY_STRING" in ev


# ---------------------------------------------------------------------------
# can_handle() — negative detection
# ---------------------------------------------------------------------------


class TestCanHandleFalse:
    def test_wrong_sizes(self):
        for size in (0, 0xFFFF, 0x10001, 0x20000):
            assert not EXTRACTOR.can_handle(build_gs20(size=size))

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_missing_header(self):
        assert not EXTRACTOR.can_handle(build_gs20(header=None))

    def test_missing_footer(self):
        assert not EXTRACTOR.can_handle(build_gs20(footer=None))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_gs20())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_gs20()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_software_version(self):
        assert self._result()["software_version"] == "7557997"

    def test_calibration_id(self):
        assert self._result()["calibration_id"] == "GADC9010"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "GS20"

    def test_match_key(self):
        assert self._result()["match_key"] == "GS20::7557997"

    def test_hardware_number_none(self):
        # Oracle HWNR 7544721 is not stored as ASCII in the dumps.
        assert self._result()["hardware_number"] is None

    def test_deterministic(self):
        assert self._result() == self._result()

    def test_filename_does_not_affect_fields(self):
        a = EXTRACTOR.extract(build_gs20(), "one.bin")
        b = EXTRACTOR.extract(build_gs20(), "two.bin")
        assert a["software_version"] == b["software_version"]
        assert a["match_key"] == b["match_key"]


# ---------------------------------------------------------------------------
# Corpus (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_GS20, reason="tests/data/ECUs/Siemens/GS20 corpus missing")
class TestCorpus:
    @pytest.mark.parametrize(
        "path",
        sorted(glob.glob(str(DATA / "ECUs" / "Siemens" / "GS20" / "*.bin"))),
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Siemens"
        assert r["ecu_family"] == "GS20"
        assert r["match_key"] == f"GS20::{r['software_version'].upper()}"
