"""
Tests for DensoSH72531Extractor (1 MB Subaru application).

Covers:
  - Identity properties: name ("Denso"), supported_families ("SH72531")
  - can_handle():
      * True  — synthetic 1 MB bins for every layout: A1 (0x41/0x61/0xA1
                markers), A2 (0xA2 marker, " L" and BF7C prefixes), and
                shifted dumps (+0x9B, +0xD0)
      * False — wrong sizes, bad descriptors, bad prefixes, bad CAL ID
                shape, missing DENSO anchor
  - extract():
      * required fields always present
      * software_version == CAL ID at the layout-correct offset
      * calibration_id == internal ID
      * match_key == "SH72531::<CAL>"
  - Corpus (skip-guarded): every 1 MB (or shifted) file in
    tests/data/ECUs/Subaru that is not a diesel is claimed by this
    extractor and yields a non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.denso.sh72531 import (
    DensoSH72531Extractor,
    NOMINAL_SIZE,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

EXTRACTOR = DensoSH72531Extractor()

SIZE = NOMINAL_SIZE


def build_sh72531(
    size: int = SIZE,
    marker: bytes = b"\x34\xa1\x00\x04",
    cal: bytes = b"A8DH100E",
    internal: bytes = b"85DK4_A",
    a2_prefix: bytes | None = None,
) -> bytes:
    """Build a valid Denso SH72531-style binary."""
    delta = size - NOMINAL_SIZE
    buf = bytearray([0xFF] * size)
    buf[delta + 0x1FFC : delta + 0x2000] = marker
    cal_off = delta + 0x2000
    if a2_prefix is not None:
        buf[cal_off : cal_off + 4] = a2_prefix
        cal_off = delta + 0x2004
    buf[cal_off : cal_off + len(cal)] = cal
    # The internal ID field is always 8 bytes, space-padded.
    padded_internal = internal + b" " * (8 - len(internal))
    buf[cal_off + 9 : cal_off + 17] = padded_internal
    copyr = b"Copr.DENSO2007"
    buf[cal_off + 0x23 : cal_off + 0x23 + len(copyr)] = copyr
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_denso(self):
        assert EXTRACTOR.name == "Denso"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["SH72531"]

    def test_repr_contains_manufacturer(self):
        assert "Denso" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_a1_marker_a1(self):
        assert EXTRACTOR.can_handle(build_sh72531(marker=b"\x34\xa1\x00\x04"))

    def test_a1_marker_41(self):
        assert EXTRACTOR.can_handle(build_sh72531(marker=b"\x34\x41\x00\x01"))

    def test_a1_marker_61(self):
        assert EXTRACTOR.can_handle(build_sh72531(marker=b"\x34\x61\x00\x01"))

    def test_a1_marker_33(self):
        assert EXTRACTOR.can_handle(build_sh72531(marker=b"\x33\x41\x00\x01"))

    def test_a2_layout_with_l_prefix(self):
        assert EXTRACTOR.can_handle(
            build_sh72531(
                marker=b"\x34\xa2\x00\x03",
                cal=b"AZ1J500T",
                internal=b"PD5H4T K",
                a2_prefix=b"\xff\xff\x20\x4c",
            )
        )

    def test_a2_layout_with_bf7c_prefix(self):
        assert EXTRACTOR.can_handle(
            build_sh72531(
                marker=b"\x34\xa2\x00\x03",
                cal=b"EZ1E102G",
                a2_prefix=b"\xff\xff\xbf\x7c",
            )
        )

    def test_shifted_dump(self):
        assert EXTRACTOR.can_handle(build_sh72531(size=SIZE + 0x9B, marker=b"\x34\xa2\x00\x03", cal=b"AE5I410A", a2_prefix=b"\xff\xff\x20\x4c"))

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_sh72531())
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
        for size in (0x80000, 0x100100, 0x180000, 0x200000):
            assert not EXTRACTOR.can_handle(b"\xff" * size)

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_bad_marker_byte0(self):
        assert not EXTRACTOR.can_handle(build_sh72531(marker=b"\x35\xa1\x00\x04"))

    def test_bad_marker_byte1(self):
        assert not EXTRACTOR.can_handle(build_sh72531(marker=b"\x34\xa3\x00\x04"))

    def test_bad_marker_tail(self):
        assert not EXTRACTOR.can_handle(build_sh72531(marker=b"\x34\xa1\x00\x05"))

    def test_a2_bad_prefix(self):
        assert not EXTRACTOR.can_handle(
            build_sh72531(marker=b"\x34\xa2\x00\x03", a2_prefix=b"\xff\xff\xff\xff")
        )

    def test_bad_cal_shape(self):
        assert not EXTRACTOR.can_handle(build_sh72531(cal=b"12345678"))

    def test_missing_deniso(self):
        data = bytearray(build_sh72531())
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
        return EXTRACTOR.extract(build_sh72531())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_sh72531()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_software_version_a1(self):
        assert self._result()["software_version"] == "A8DH100E"

    def test_software_version_a2(self):
        r = EXTRACTOR.extract(
            build_sh72531(
                marker=b"\x34\xa2\x00\x03",
                cal=b"AZ1J500T",
                a2_prefix=b"\xff\xff\x20\x4c",
            )
        )
        assert r["software_version"] == "AZ1J500T"

    def test_software_version_shifted(self):
        r = EXTRACTOR.extract(
            build_sh72531(
                size=SIZE + 0x9B,
                marker=b"\x34\xa2\x00\x03",
                cal=b"AE5I410A",
                a2_prefix=b"\xff\xff\x20\x4c",
            )
        )
        assert r["software_version"] == "AE5I410A"

    def test_calibration_id(self):
        assert self._result()["calibration_id"] == "85DK4_A"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "SH72531"

    def test_match_key(self):
        assert self._result()["match_key"] == "SH72531::A8DH100E"

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
            if NOMINAL_SIZE <= Path(f).stat().st_size <= NOMINAL_SIZE + 0xFF
            and "Diesel" not in Path(f).name
        ],
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Denso"
        assert r["ecu_family"] == "SH72531"
        assert r["match_key"] == f"SH72531::{r['software_version'].upper()}"
