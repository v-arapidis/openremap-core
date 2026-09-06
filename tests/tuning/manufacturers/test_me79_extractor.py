"""Tests for the Bosch ME7.9.x (ME7.9.10) extractor."""

import glob
from pathlib import Path

import pytest

from openremap.core.manufacturers.bosch.me79.extractor import BoschME79Extractor

EXTRACTOR = BoschME79Extractor()

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_ME791 = (DATA / "ECUs" / "Bosch" / "ME7.9.1").is_dir()

SIZE_832KB = 0xD0000  # 851,968 bytes — real ME7.9.10 dump size


def build_me79(size: int = SIZE_832KB, family: bool = True) -> bytes:
    """Synthetic ME7.9.x dump: ZZ<< dialect at 0x10000 + optional marker."""
    buf = bytearray(size)
    buf[0x10000:0x10004] = b"ZZ<<"
    buf[0x10004:0x10014] = b"ERCSU V1.1.6 SAB"  # 16 printable chars
    if family:
        buf[0x30000:0x30020] = b"45/1/ME7910/9/F200M144//3207C"
    return bytes(buf)


class TestCanHandle:
    def test_rejects_too_small(self):
        assert EXTRACTOR.can_handle(b"\x00" * 0x10000) is False

    def test_rejects_wrong_dialect(self):
        buf = bytearray(SIZE_832KB)
        buf[0x10000:0x10004] = b"ZZ0"  # classic ME7 "ZZ<digit>" dialect
        buf[0x10004:0x10014] = b"0" * 16
        assert EXTRACTOR.can_handle(bytes(buf)) is False

    def test_rejects_non_printable_after_dialect(self):
        buf = bytearray(SIZE_832KB)
        buf[0x10000:0x10004] = b"ZZ<<"
        assert EXTRACTOR.can_handle(bytes(buf)) is False

    def test_accepts_synthetic_with_marker(self):
        data = build_me79()
        assert EXTRACTOR.can_handle(data) is True
        ev = EXTRACTOR.last_detection_evidence
        assert "SIZE_MATCH" in ev and "MAGIC_MATCH" in ev and "HEADER_MATCH" in ev
        assert "FAMILY_STRING" in ev

    def test_accepts_without_marker(self):
        data = build_me79(family=False)
        assert EXTRACTOR.can_handle(data) is True
        assert "FAMILY_STRING" not in EXTRACTOR.last_detection_evidence


class TestExtract:
    def test_extract_fields_synthetic(self):
        buf = bytearray(build_me79())
        buf[0x10000 + 0x20:0x10000 + 0x2A] = b"1037392099"
        buf[0x10000 + 0x40:0x10000 + 0x4A] = b"0261201685"
        result = EXTRACTOR.extract(bytes(buf))
        assert result["ecu_family"] == "ME7.9.10"
        assert result["software_version"] == "1037392099"
        assert result["hardware_number"] == "0261201685"
        assert result["match_key"] == "ME7.9.10::1037392099"

    def test_extract_sw_with_zero_padding(self):
        """SW field followed by zero padding still resolves."""
        buf = bytearray(build_me79())
        buf[0x10000 + 0x20:0x10000 + 0x2E] = b"1037392099000000"
        result = EXTRACTOR.extract(bytes(buf))
        assert result["software_version"] == "1037392099"

    def test_extract_hw_inside_longer_field(self):
        """HW number embedded in a longer alnum field still resolves."""
        buf = bytearray(build_me79())
        buf[0x10000 + 0x20:0x10000 + 0x2C] = b"70261S04662 "
        result = EXTRACTOR.extract(bytes(buf))
        assert result["hardware_number"] == "0261S04662"


@pytest.mark.skipif(not HAS_ME791, reason="tests/data/ECUs/Bosch/ME7.9.1 corpus missing")
class TestCorpus:
    @pytest.mark.parametrize(
        "pattern",
        ["*Abarth*0261201685_1037392099*", "*Abarth*0261S04662_1037400604*"],
    )
    def test_real_abarth_dumps(self, pattern):
        hits = glob.glob(str(DATA / "ECUs" / "Bosch" / "ME7.9.1" / pattern))
        assert hits, f"no corpus file matching {pattern}"
        data = Path(hits[0]).read_bytes()
        assert EXTRACTOR.can_handle(data) is True
        result = EXTRACTOR.extract(data)
        assert result["ecu_family"] == "ME7.9.10"
        assert result["software_version"] is not None
        assert result["hardware_number"] is not None
