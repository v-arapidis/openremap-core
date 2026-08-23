"""
Tests for DensoSH7055Extractor (16-bit Subaru application).

Covers:
  - Identity properties: name ("Denso"), supported_families ("SH7055")
  - can_handle():
      * True  — synthetic 160 KB and 192 KB bins with valid marker,
                CAL ID, and "CopyrightDENSO" anchor
      * False — wrong sizes, missing 0x02 0x40 marker, bad CAL ID shape,
                missing copyright anchor
  - extract():
      * required fields always present (manufacturer, file_size, md5,
        sha256_first_64kb)
      * software_version == CAL ID at 0x200
      * match_key == "SH7055::<CAL>"
      * deterministic; filename does not affect identification fields
  - Corpus (skip-guarded): every 160/192 KB file in tests/data/ECUs/Subaru
    is claimed by this extractor and yields a non-empty software_version.
"""

import glob
import hashlib
from pathlib import Path

import pytest

from openremap.core.manufacturers.denso.sh7055 import (
    CAL_MARKER,
    DensoSH7055Extractor,
)

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()

EXTRACTOR = DensoSH7055Extractor()

SIZE_160KB = 0x28000
SIZE_192KB = 0x30000


def build_sh7055(size: int = SIZE_192KB, cal: bytes = b"A4RG060P") -> bytes:
    """Build a valid Denso SH7055-style binary."""
    buf = bytearray([0xFF] * size)
    buf[0x1FE:0x200] = CAL_MARKER
    buf[0x200 : 0x200 + len(cal)] = cal
    copyr = b"CACopyrightDENSO2002"
    buf[0x209 : 0x209 + len(copyr)] = copyr
    return bytes(buf)


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_denso(self):
        assert EXTRACTOR.name == "Denso"

    def test_supported_families(self):
        assert EXTRACTOR.supported_families == ["SH7055"]

    def test_repr_contains_manufacturer(self):
        assert "Denso" in repr(EXTRACTOR)


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    def test_192kb(self):
        assert EXTRACTOR.can_handle(build_sh7055(SIZE_192KB))

    def test_160kb(self):
        assert EXTRACTOR.can_handle(build_sh7055(SIZE_160KB))

    def test_evidence_tags(self):
        assert EXTRACTOR.can_handle(build_sh7055())
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
        for size in (0x20000, 0x28001, 0x2FFFF, 0x30001, 0x40000, 0x80000, 0x100000):
            assert not EXTRACTOR.can_handle(build_sh7055(size))

    def test_empty(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_missing_marker(self):
        data = bytearray(build_sh7055())
        data[0x1FE:0x200] = b"\x00\x00"
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_bad_cal_shape(self):
        data = bytearray(build_sh7055())
        data[0x200:0x208] = b"12345678"
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_missing_copyright(self):
        data = bytearray(build_sh7055())
        data[0x209:0x222] = b"\x00" * 0x19
        assert not EXTRACTOR.can_handle(bytes(data))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(b"\x00" * 1024)
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def _result(self) -> dict:
        return EXTRACTOR.extract(build_sh7055())

    def test_required_fields(self):
        r = self._result()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in r and r[key]

    def test_hashes(self):
        data = build_sh7055()
        r = EXTRACTOR.extract(data)
        assert r["md5"] == hashlib.md5(data).hexdigest()
        assert r["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()
        assert len(r["sha256_first_64kb"]) == 64

    def test_software_version(self):
        assert self._result()["software_version"] == "A4RG060P"

    def test_truncated_cal_strips_padding(self):
        data = bytearray(build_sh7055())
        data[0x200:0x208] = b"A4RG06  "
        r = EXTRACTOR.extract(bytes(data))
        assert r["software_version"] == "A4RG06"

    def test_ecu_family(self):
        assert self._result()["ecu_family"] == "SH7055"

    def test_match_key(self):
        assert self._result()["match_key"] == "SH7055::A4RG060P"

    def test_deterministic(self):
        assert self._result() == self._result()

    def test_filename_does_not_affect_fields(self):
        a = EXTRACTOR.extract(build_sh7055(), "one.hex")
        b = EXTRACTOR.extract(build_sh7055(), "two.bin")
        assert a["software_version"] == b["software_version"]
        assert a["match_key"] == b["match_key"]


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
            if Path(f).stat().st_size in (SIZE_160KB, SIZE_192KB)
        ],
        ids=lambda p: Path(p).name,
    )
    def test_corpus_file_claimed_and_extracted(self, path):
        data = Path(path).read_bytes()
        assert EXTRACTOR.can_handle(data)
        r = EXTRACTOR.extract(data)
        assert r["software_version"]
        assert r["manufacturer"] == "Denso"
        assert r["ecu_family"] == "SH7055"
        assert r["match_key"] == f"SH7055::{r['software_version'].upper()}"
