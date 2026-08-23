"""
Tests for HitachiSH72546Extractor (1.5/2 MB Subaru application).

Covers:
  - Identity properties: name ("Hitachi"), supported_families ("SH72546")
  - can_handle():
      * True  — synthetic 1.5/2 MB bins with a "T\\x00"-tagged CAL ID
                followed by an engine descriptor
      * False — wrong sizes, missing tag, bad CAL shape, engine text
                missing, decoy "T\\x00" strings without engine text
  - extract():
      * required fields always present
      * software_version == CAL ID after the "T\\x00" tag
      * match_key == "SH72546::<CAL>"
  - Corpus (skip-guarded): every 1.5/2 MB file in tests/data/ECUs/Subaru
    is claimed by this extractor and yields a non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.hitachi.sh72546 import HitachiSH72546Extractor

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

EXTRACTOR = HitachiSH72546Extractor()

SIZE_15MB = 0x180000
SIZE_2MB = 0x200000


def build_sh72546(
    size: int = SIZE_2MB,
    cal: bytes = b"LV9N100B",
    engine_text: bytes = b"2.0 TURBO",
    cal_offset: int = 0x2AA20,
) -> bytes:
    """Build a valid Hitachi SH72546-style binary."""
    buf = bytearray([0xFF] * size)
    buf[cal_offset - 2 : cal_offset] = b"T\x00"
    buf[cal_offset : cal_offset + len(cal)] = cal
    buf[cal_offset + 8 : cal_offset + 8 + len(engine_text)] = engine_text
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_hitachi(self):
        assert EXTRACTOR.name == "Hitachi"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["SH72546"]

    def test_repr_contains_manufacturer(self):
        assert "Hitachi" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_2mb(self):
        assert EXTRACTOR.can_handle(build_sh72546(SIZE_2MB))

    def test_15mb(self):
        assert EXTRACTOR.can_handle(build_sh72546(SIZE_15MB, cal=b"AF5G200A"))

    def test_engine_text_after_nuls(self):
        data = bytearray(build_sh72546())
        data[0x2AA28:0x2AA2C] = b"\x00\x00\x00\x00"
        data[0x2AA2C:0x2AA35] = b"2.0 TURBO"
        assert EXTRACTOR.can_handle(bytes(data))

    def test_engine_text_directly_after(self):
        assert EXTRACTOR.can_handle(
            build_sh72546(cal=b"AF5G200A", engine_text=b"2.0 TURBO", cal_offset=0x18FC4)
        )

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_sh72546())
        ev = set(EXTRACTOR.last_detection_evidence)
        assert "SIZE_MATCH" in ev
        assert "MAGIC_MATCH" in ev
        assert "IDENT_BLOCK" in ev


# ---------------------------------------------------------------------------
# can_handle() — negative detection
# ---------------------------------------------------------------------------


class TestCanHandleFalse:
    def test_wrong_sizes(self):
        for size in (0x100000, 0x1000D0, 0x17FFFF, 0x180001, 0x1FFFFF):
            assert not EXTRACTOR.can_handle(b"\xff" * size)

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_no_tag(self):
        assert not EXTRACTOR.can_handle(build_sh72546(cal_offset=0))

    def test_decoy_tag_without_engine_text(self):
        data = bytearray(build_sh72546())
        data[0x10000:0x10002] = b"T\x00"
        data[0x10002:0x1000A] = b"C0C0C0CQ"
        # The decoy matches the tag+shape but is not followed by an engine
        # descriptor, so the real CAL ID must still win.
        assert EXTRACTOR.can_handle(bytes(data))
        r = EXTRACTOR.extract(bytes(data))
        assert r["software_version"] == "LV9N100B"

    def test_bad_cal_shape(self):
        assert not EXTRACTOR.can_handle(build_sh72546(cal=b"12345678"))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_sh72546())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_sh72546()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_software_version(self):
        assert self._result()["software_version"] == "LV9N100B"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "SH72546"

    def test_match_key(self):
        assert self._result()["match_key"] == "SH72546::LV9N100B"

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
            if Path(f).stat().st_size in (SIZE_15MB, SIZE_2MB)
        ],
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Hitachi"
        assert r["ecu_family"] == "SH72546"
        assert r["match_key"] == f"SH72546::{r['software_version'].upper()}"
