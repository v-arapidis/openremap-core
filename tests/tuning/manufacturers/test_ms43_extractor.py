"""
Tests for SiemensMS43Extractor (MS43).

Covers:
  - Identity properties: name, supported_families, repr, detection strength
  - Evidence-based detection: SIZE_MATCH / FAMILY_ANCHOR /
    DETECTION_SIGNATURE / EXCLUSION_CLEAR on the True path, cleared on False
  - can_handle():
      * True  — MS43 + 5WK9, MS43 + ca43...DAT (past the 128 KB window),
        full ident record
      * False — wrong file size (incl. 128 KB Simtec56-sized bins that
        carry the shared 5WK9 prefix — the size gate is the disambiguator)
      * False — MS43 only (no secondary signature) and 5WK9 only (no MS43
        anchor)
      * False — correct size but no signatures
      * False — correct size with exclusion signatures (EDC17, MED17,
        ME7., ME9, SID801, SID803, 5WS4, PPD, SIMOS, BOSCH)
      * False — signature outside the detection region (past 0x20000)
  - extract():
      * Required fields always present: manufacturer, file_size, md5,
        sha256_first_64kb
      * hardware_number "5WK90027" from the 5WK9 ident record
      * software_version "430069" (program number) — ca...DAT capture
        first, fixed offset 0x6FFBA fallback, priority ordering
      * ecu_family "MS43"
      * calibration_id "ca430069.DAT"
      * serial_number "1061330037"
      * oem_part_number from a standalone 7-digit run (absent on corpus)
      * ident_block (0x3F80, 0x3FC0)
      * match_key "MS43::430069"
      * match_key is None when no software_version found
  - build_match_key() (base-class behaviour)
  - Determinism: same binary → same result; filename-independent
  - Pattern compile / region validity sanity
  - Resolver unit tests
  - Registry integration: in the Siemens EXTRACTORS list after Simtec56 and
    before EMS2000, and in the global manufacturer registry
  - Corpus-gated integration (pytest.skip when tests/data is absent):
    identify_ecu returns Siemens / MS43 / 430069 / 5WK90027 / MS43::430069
    on the real base binary, and all 4 corpus files produce the SAME
    identity (the tune-safety check).
"""

import hashlib
import re
from pathlib import Path

import pytest

from openremap.core.manufacturers.base import (
    DETECTION_SIGNATURE,
    EXCLUSION_CLEAR,
    FAMILY_ANCHOR,
    SIZE_MATCH,
    DetectionStrength,
)
from openremap.core.manufacturers.siemens.ms43.extractor import (
    SiemensMS43Extractor,
)
from openremap.core.manufacturers.siemens.ms43.patterns import (
    DETECTION_SIGNATURES,
    EXCLUSION_SIGNATURES,
    IDENT_BLOCK,
    MS43_FILE_SIZE,
    PATTERNS,
    PATTERN_REGIONS,
    PROGRAM_NUMBER_OFFSET,
    SEARCH_REGIONS,
    SWID_OFFSET,
    SW_NUMBER_TAIL_OFFSET,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def make_bin(size: int = MS43_FILE_SIZE, fill: int = 0x00) -> bytearray:
    """Return a mutable bytearray of `size` bytes set to `fill`."""
    return bytearray([fill] * size)


def write(buf: bytearray, offset: int, data: bytes) -> bytearray:
    """Write `data` at `offset` in `buf` and return `buf`."""
    buf[offset : offset + len(data)] = data
    return buf


# ---------------------------------------------------------------------------
# Sizes (bytes)
# ---------------------------------------------------------------------------

KB = 1024
MB = 1024 * KB

# MS43 expected size — exactly 512 KB
SIZE_MS43 = MS43_FILE_SIZE  # 524288
assert SIZE_MS43 == 512 * KB

# Realistic ident record from a real MS43 binary (verified at 0x3F80)
IDENT_RECORD = b"5WK90027--1061330037MS43060414051158416577357604b-6577355117--"

EXTRACTOR = SiemensMS43Extractor()


def _ms43_bin(ms43: bytes = b"MS43", wk9: bytes = b"5WK90027") -> bytes:
    """A 512 KB bin with the MS43 anchor and 5WK9 hardware prefix."""
    buf = make_bin()
    write(buf, 0x100, ms43)
    write(buf, 0x200, wk9)
    return bytes(buf)


def _full_bin() -> bytes:
    """A realistic MS43 bin: ident record + calibration dataset reference."""
    buf = make_bin()
    write(buf, 0x3F80, IDENT_RECORD)
    write(buf, 0x70040, b"ca430069.DAT")
    return bytes(buf)


# ---------------------------------------------------------------------------
# Corpus helpers (skip-guarded integration)
# ---------------------------------------------------------------------------

DATA_DIR = Path("tests/data")
MS43_CORPUS_DIR = DATA_DIR / "ECUs" / "Siemens" / "MS43"


def _corpus_files() -> list:
    if not MS43_CORPUS_DIR.is_dir():
        return []
    return sorted(MS43_CORPUS_DIR.glob("*.bin"))


def _corpus_base_file() -> Path:
    """The factory base binary (falls back to the first corpus file)."""
    files = _corpus_files()
    if not files:
        return None
    for f in files:
        if "WBABW510X0PK46741" in f.name or "_512KB" in f.name:
            return f
    return files[0]


# ---------------------------------------------------------------------------
# Identity properties
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_name_is_siemens(self):
        assert EXTRACTOR.name == "Siemens"

    def test_name_is_string(self):
        assert isinstance(EXTRACTOR.name, str)

    def test_supported_families_is_list(self):
        assert isinstance(EXTRACTOR.supported_families, list)

    def test_supported_families_not_empty(self):
        assert len(EXTRACTOR.supported_families) > 0

    def test_ms43_in_supported_families(self):
        assert "MS43" in EXTRACTOR.supported_families

    def test_all_families_are_strings(self):
        for fam in EXTRACTOR.supported_families:
            assert isinstance(fam, str)

    def test_detection_strength_is_strong(self):
        assert EXTRACTOR.detection_strength == DetectionStrength.STRONG

    def test_repr_contains_manufacturer(self):
        r = repr(EXTRACTOR)
        assert "Siemens" in r

    def test_repr_contains_class_name(self):
        r = repr(EXTRACTOR)
        assert "SiemensMS43Extractor" in r


# ---------------------------------------------------------------------------
# can_handle() — positive detection
# ---------------------------------------------------------------------------


class TestCanHandleTrue:
    """Binary is correct size and carries the MS43 anchor + a secondary."""

    def test_ms43_and_5wk9(self):
        assert EXTRACTOR.can_handle(_ms43_bin())

    def test_full_ident_record(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_ms43_and_calibration_dataset(self):
        # ca43...DAT lives at 0x70040 — past the 128 KB detection window;
        # must still count as a secondary signature.
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x70040, b"ca430069.DAT")
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_signature_at_offset_zero(self):
        buf = make_bin()
        write(buf, 0, IDENT_RECORD)
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_ms43_near_end_of_detection_region(self):
        # Detection region is first 128 KB — place MS43 near the end.
        buf = make_bin()
        offset = 0x1FFFC  # 0x20000 - 4 = last position where MS43 fits
        write(buf, offset, b"MS43")
        write(buf, 0x100, b"5WK90027")
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_5wk9_near_end_of_detection_region(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        offset = 0x1FFFB  # 0x20000 - 5 = last position where 5WK9 fits
        write(buf, offset, b"5WK9")
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_multiple_signatures_still_true(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca430069.DAT")
        assert EXTRACTOR.can_handle(bytes(buf))

    def test_evidence_emitted_on_true(self):
        EXTRACTOR.can_handle(_ms43_bin())
        assert set(EXTRACTOR.last_detection_evidence) == {
            SIZE_MATCH,
            FAMILY_ANCHOR,
            DETECTION_SIGNATURE,
            EXCLUSION_CLEAR,
        }


# ---------------------------------------------------------------------------
# can_handle() — negative: wrong size
# ---------------------------------------------------------------------------


class TestCanHandleFalseWrongSize:
    """Size gate must reject binaries that are not exactly 524288 bytes."""

    def test_empty_binary(self):
        assert not EXTRACTOR.can_handle(b"")

    def test_1_byte_binary(self):
        assert not EXTRACTOR.can_handle(b"\x00")

    def test_128kb_with_ms43_and_5wk9(self):
        # Simtec56-sized bin (128 KB) with the shared 5WK9 prefix — the
        # size gate is what disambiguates MS43 from Simtec56.
        buf = make_bin(128 * KB)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_256kb_with_signatures(self):
        buf = make_bin(256 * KB)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_1mb_with_signatures(self):
        buf = make_bin(1 * MB)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_64kb(self):
        buf = make_bin(64 * KB)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_one_byte_less_than_512kb(self):
        buf = make_bin(SIZE_MS43 - 1)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_one_byte_more_than_512kb(self):
        buf = make_bin(SIZE_MS43 + 1)
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))


# ---------------------------------------------------------------------------
# can_handle() — negative: missing positive anchor
# ---------------------------------------------------------------------------


class TestCanHandleFalseNoAnchor:
    """Correct size but the MS43 anchor / secondary signature is missing."""

    def test_all_zero_binary(self):
        assert not EXTRACTOR.can_handle(bytes(make_bin()))

    def test_all_ff_binary(self):
        assert not EXTRACTOR.can_handle(bytes(make_bin(fill=0xFF)))

    def test_random_like_bytes_no_signature(self):
        buf = make_bin()
        pattern = b"ABCDEFGH" * (SIZE_MS43 // 8)
        buf[: len(pattern)] = pattern[:SIZE_MS43]
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_ascii_noise_no_signature(self):
        buf = make_bin()
        noise = b"This is just some text with no ECU signatures at all." * 100
        write(buf, 0x100, noise[:0x1000])
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_5wk9_without_ms43_anchor(self):
        # 5WK9 is NOT unique to MS43 (Simtec56 shares it) — without the
        # MS43 anchor this must be rejected even at the right size.
        buf = make_bin()
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_ms43_without_secondary(self):
        # MS43 alone is not enough — the plan requires at least one of
        # {5WK9, ca43...DAT} alongside the anchor.
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_signature_outside_detection_region(self):
        # MS43/5WK9 past the 128 KB detection region → rejected.
        buf = make_bin()
        write(buf, 0x30000, b"MS43")
        write(buf, 0x30010, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_evidence_cleared_on_false(self):
        EXTRACTOR.can_handle(_ms43_bin())  # prime evidence
        EXTRACTOR.can_handle(bytes(make_bin()))  # reject
        assert EXTRACTOR.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# can_handle() — negative: exclusion signatures
# ---------------------------------------------------------------------------


class TestCanHandleFalseExclusion:
    """Exclusion signatures override positive detection → False."""

    def _make_with_exclusion(self, exclusion_sig: bytes) -> bytes:
        buf = make_bin()
        write(buf, 0x100, b"MS43")  # positive anchor
        write(buf, 0x200, b"5WK90027")  # positive secondary
        write(buf, 0x50000, exclusion_sig)  # exclusion signature
        return bytes(buf)

    def test_edc17_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"EDC17"))

    def test_medc17_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"MEDC17"))

    def test_med17_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"MED17"))

    def test_me7_dot_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"ME7."))

    def test_me9_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"ME9"))

    def test_sid803_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"SID803"))

    def test_sid801_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"SID801"))

    def test_5ws4_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"5WS4"))

    def test_ppd_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"PPD"))

    def test_simos_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"SIMOS"))

    def test_bosch_exclusion(self):
        assert not EXTRACTOR.can_handle(self._make_with_exclusion(b"BOSCH"))

    def test_exclusion_overrides_multiple_detections(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x60000, b"ME7.")  # exclusion
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_exclusion_at_start_of_binary(self):
        buf = make_bin()
        write(buf, 0x000, b"SID801")
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_exclusion_at_end_of_binary(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, SIZE_MS43 - 10, b"EDC17")
        assert not EXTRACTOR.can_handle(bytes(buf))

    def test_all_exclusion_signatures_defined(self):
        """Every exclusion signature rejects when present."""
        for sig in EXCLUSION_SIGNATURES:
            data = self._make_with_exclusion(sig)
            assert not EXTRACTOR.can_handle(data), f"Exclusion {sig!r} should reject"


# ---------------------------------------------------------------------------
# extract() — required fields
# ---------------------------------------------------------------------------


class TestExtractRequiredFields:
    """All required fields present and correctly computed."""

    def _extract(self, data: bytes = None) -> dict:
        if data is None:
            data = _full_bin()
        return EXTRACTOR.extract(data, "test.bin")

    def test_all_required_fields_present(self):
        result = self._extract()
        for key in ("manufacturer", "file_size", "md5", "sha256_first_64kb"):
            assert key in result, f"Missing required field: {key}"

    def test_manufacturer_always_siemens(self):
        assert self._extract()["manufacturer"] == "Siemens"

    def test_file_size_equals_data_length(self):
        data = _full_bin()
        result = EXTRACTOR.extract(data, "test.bin")
        assert result["file_size"] == len(data)

    def test_file_size_is_512kb(self):
        assert self._extract()["file_size"] == SIZE_MS43

    def test_md5_is_32_hex_chars(self):
        md5 = self._extract()["md5"]
        assert len(md5) == 32
        assert re.match(r"^[0-9a-f]{32}$", md5)

    def test_md5_is_lowercase_hex(self):
        md5 = self._extract()["md5"]
        assert md5 == md5.lower()

    def test_md5_matches_hashlib(self):
        data = _full_bin()
        result = EXTRACTOR.extract(data, "test.bin")
        assert result["md5"] == hashlib.md5(data).hexdigest()

    def test_sha256_first_64kb_is_64_hex_chars(self):
        sha = self._extract()["sha256_first_64kb"]
        assert len(sha) == 64
        assert re.match(r"^[0-9a-f]{64}$", sha)

    def test_sha256_first_64kb_matches_hashlib(self):
        data = _full_bin()
        result = EXTRACTOR.extract(data, "test.bin")
        assert result["sha256_first_64kb"] == hashlib.sha256(data[:0x10000]).hexdigest()

    def test_sha256_first_64kb_uses_only_first_64kb(self):
        buf1 = make_bin()
        write(buf1, 0x3F80, IDENT_RECORD)
        buf2 = bytearray(buf1)
        # Change a byte after the first 64 KB
        buf2[0x10001] = 0xAB
        result1 = EXTRACTOR.extract(bytes(buf1), "test1.bin")
        result2 = EXTRACTOR.extract(bytes(buf2), "test2.bin")
        # sha256_first_64kb should be the same
        assert result1["sha256_first_64kb"] == result2["sha256_first_64kb"]
        # But md5 of the full file should differ
        assert result1["md5"] != result2["md5"]

    def test_md5_changes_with_different_content(self):
        buf1 = make_bin()
        write(buf1, 0x3F80, IDENT_RECORD)
        buf2 = make_bin()
        write(buf2, 0x3F80, b"5WK90027--1061330038MS43")
        r1 = EXTRACTOR.extract(bytes(buf1), "a.bin")
        r2 = EXTRACTOR.extract(bytes(buf2), "b.bin")
        assert r1["md5"] != r2["md5"]


# ---------------------------------------------------------------------------
# extract() — hardware_number
# ---------------------------------------------------------------------------


class TestExtractHardwareNumber:
    def test_hardware_number_from_ident_record(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["hardware_number"] == "5WK90027"

    def test_hardware_number_standalone(self):
        buf = make_bin()
        write(buf, 0x100, b"5WK90027")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["hardware_number"] == "5WK90027"

    def test_hardware_number_five_digit_variant(self):
        buf = make_bin()
        write(buf, 0x100, b"5WK900271")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["hardware_number"] == "5WK900271"

    def test_hardware_number_absent_returns_none(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["hardware_number"] is None


# ---------------------------------------------------------------------------
# extract() — software_version (the program number)
# ---------------------------------------------------------------------------


class TestExtractSoftwareVersion:
    def test_program_number_from_calibration_dataset(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca430069.DAT")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] == "430069"

    def test_program_number_variant_8_digits(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca43003701.DAT")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] == "43003701"

    def test_fixed_offset_fallback(self):
        # No ca...DAT reference — program number from the tail ident block.
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, 0x6FFBA, b"430069")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] == "430069"

    def test_fixed_offset_reads_exactly_six_digits(self):
        # A longer digit run at 0x6FFBA must still resolve to the first 6.
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, 0x6FFBA, b"430069657735")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] == "430069"

    def test_calibration_dataset_takes_priority_over_fixed_offset(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca43003701.DAT")
        write(buf, 0x6FFBA, b"430069")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] == "43003701"

    def test_sw_version_absent_when_no_source(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["software_version"] is None


# ---------------------------------------------------------------------------
# extract() — ecu_family
# ---------------------------------------------------------------------------


class TestExtractEcuFamily:
    def test_family_from_literal(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["ecu_family"] == "MS43"

    def test_family_defaults_to_ms43(self):
        buf = make_bin()
        write(buf, 0x100, b"5WK90027")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["ecu_family"] == "MS43"

    def test_family_is_string(self):
        assert isinstance(EXTRACTOR.extract(_full_bin(), "t.bin")["ecu_family"], str)


# ---------------------------------------------------------------------------
# extract() — calibration_id
# ---------------------------------------------------------------------------


class TestExtractCalibrationId:
    def test_calibration_dataset_detected(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca430069.DAT")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["calibration_id"] == "ca430069.DAT"

    def test_calibration_dataset_absent_returns_none(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["calibration_id"] is None


# ---------------------------------------------------------------------------
# extract() — serial_number
# ---------------------------------------------------------------------------


class TestExtractSerialNumber:
    def test_serial_from_ident_record(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["serial_number"] == "1061330037"

    def test_serial_standalone_10_digit(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, 0x2000, b"1234567890")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["serial_number"] == "1234567890"

    def test_serial_absent_returns_none(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027--")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["serial_number"] is None

    def test_serial_is_ten_digits(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert result["serial_number"] is not None
        assert re.match(r"^\d{10}$", result["serial_number"])


# ---------------------------------------------------------------------------
# extract() — oem_part_number
# ---------------------------------------------------------------------------


class TestExtractOemPartNumber:
    def test_oem_part_number_detected(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        write(buf, 0x6FE80, b"7551615")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["oem_part_number"] == "7551615"

    def test_oem_part_number_absent_returns_none(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["oem_part_number"] is None


# ---------------------------------------------------------------------------
# extract() — ident_block
# ---------------------------------------------------------------------------


class TestExtractIdentBlock:
    def test_ident_block_declared(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert result["ident_block"] == IDENT_BLOCK

    def test_ident_block_is_offset_pair(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        start, end = result["ident_block"]
        assert 0 <= start < end <= SIZE_MS43
        assert end - start == 0x40


# ---------------------------------------------------------------------------
# extract() — match_key
# ---------------------------------------------------------------------------


class TestExtractMatchKey:
    def test_match_key_built_when_sw_present(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert result["match_key"] is not None

    def test_match_key_format_is_family_double_colon_version(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert result["match_key"] == "MS43::430069"

    def test_match_key_none_when_no_sw(self):
        buf = make_bin()
        write(buf, 0x100, b"MS43")
        write(buf, 0x200, b"5WK90027")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["match_key"] is None

    def test_match_key_with_different_program_number(self):
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x70040, b"ca43003701.DAT")
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert result["match_key"] == "MS43::43003701"

    def test_match_key_uses_uppercase(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert result["match_key"] == result["match_key"].upper()


# ---------------------------------------------------------------------------
# extract() — fields not applicable to MS43
# ---------------------------------------------------------------------------


class TestExtractNotApplicableFields:
    def _extract(self) -> dict:
        return EXTRACTOR.extract(_full_bin(), "test.bin")

    def test_ecu_variant_is_none(self):
        assert self._extract()["ecu_variant"] is None

    def test_calibration_version_is_none(self):
        assert self._extract()["calibration_version"] is None

    def test_sw_base_version_is_none(self):
        assert self._extract()["sw_base_version"] is None

    def test_dataset_number_is_none(self):
        assert self._extract()["dataset_number"] is None


# ---------------------------------------------------------------------------
# extract() — raw_strings
# ---------------------------------------------------------------------------


class TestExtractRawStrings:
    def test_raw_strings_is_list(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert isinstance(result["raw_strings"], list)

    def test_raw_strings_contains_ident(self):
        result = EXTRACTOR.extract(_full_bin(), "test.bin")
        assert len(result["raw_strings"]) >= 1
        found = any("5WK90027" in s for s in result["raw_strings"])
        assert found

    def test_raw_strings_limited_to_20(self):
        buf = make_bin()
        for i in range(30):
            offset = 0x10 + i * 32
            s = f"TestString{i:03d}ABCDEF".encode("ascii")
            write(buf, offset, s)
        result = EXTRACTOR.extract(bytes(buf), "test.bin")
        assert len(result["raw_strings"]) <= 20


# ---------------------------------------------------------------------------
# build_match_key() — unit tests on the method directly
# ---------------------------------------------------------------------------


class TestBuildMatchKey:
    def test_family_and_sw_produces_key(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="MS43",
            software_version="430069",
        )
        assert key == "MS43::430069"

    def test_none_returned_when_no_sw(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="MS43",
            software_version=None,
        )
        assert key is None

    def test_none_returned_when_empty_sw(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="MS43",
            software_version="",
        )
        assert key is None

    def test_unknown_used_when_no_family(self):
        key = EXTRACTOR.build_match_key(
            software_version="430069",
        )
        assert key == "UNKNOWN::430069"

    def test_key_is_uppercase(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="ms43",
            software_version="430069",
        )
        assert key == key.upper()

    def test_double_colon_separator(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="MS43",
            software_version="430069",
        )
        assert "::" in key
        parts = key.split("::")
        assert len(parts) == 2

    def test_variant_takes_precedence_over_family(self):
        key = EXTRACTOR.build_match_key(
            ecu_family="MS43",
            ecu_variant="MS43C167",
            software_version="430069",
        )
        assert key.startswith("MS43C167::")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestExtractDeterminism:
    def test_same_binary_produces_same_result(self):
        data = _full_bin()
        result1 = EXTRACTOR.extract(data, "test.bin")
        result2 = EXTRACTOR.extract(data, "test.bin")
        assert result1 == result2

    def test_filename_does_not_change_identification(self):
        data = _full_bin()
        result1 = EXTRACTOR.extract(data, "original.bin")
        result2 = EXTRACTOR.extract(data, "renamed_copy.bin")
        assert result1 == result2

    def test_different_content_produces_different_md5(self):
        buf1 = make_bin()
        write(buf1, 0x3F80, IDENT_RECORD)
        buf2 = make_bin()
        write(buf2, 0x3F80, b"5WK90027--1061330038MS43")
        r1 = EXTRACTOR.extract(bytes(buf1), "a.bin")
        r2 = EXTRACTOR.extract(bytes(buf2), "b.bin")
        assert r1["md5"] != r2["md5"]


# ---------------------------------------------------------------------------
# Full realistic extraction — integration-style
# ---------------------------------------------------------------------------


class TestFullRealisticExtraction:
    """Simulate a realistic MS43 binary and verify every field."""

    def _make_full_binary(self) -> bytes:
        buf = make_bin()
        write(buf, 0x3F80, IDENT_RECORD)
        write(buf, 0x6FE80, b"7551615")
        write(buf, 0x6FFBA, b"430069")
        write(buf, 0x70040, b"ca430069.DAT")
        return bytes(buf)

    def test_all_fields_populated(self):
        result = EXTRACTOR.extract(self._make_full_binary(), "full_test.bin")
        assert result["manufacturer"] == "Siemens"
        assert result["file_size"] == SIZE_MS43
        assert result["hardware_number"] == "5WK90027"
        assert result["software_version"] == "430069"
        assert result["ecu_family"] == "MS43"
        assert result["calibration_id"] == "ca430069.DAT"
        assert result["serial_number"] == "1061330037"
        assert result["oem_part_number"] == "7551615"
        assert result["match_key"] == "MS43::430069"
        assert result["ident_block"] == IDENT_BLOCK

    def test_md5_and_sha256_present(self):
        result = EXTRACTOR.extract(self._make_full_binary(), "full_test.bin")
        assert len(result["md5"]) == 32
        assert len(result["sha256_first_64kb"]) == 64

    def test_raw_strings_present(self):
        result = EXTRACTOR.extract(self._make_full_binary(), "full_test.bin")
        assert isinstance(result["raw_strings"], list)
        assert len(result["raw_strings"]) >= 1


# ---------------------------------------------------------------------------
# Patterns — sanity checks
# ---------------------------------------------------------------------------


class TestPatterns:
    """Verify that pattern definitions compile and match expected strings."""

    def test_all_patterns_compile(self):
        for name, pattern in PATTERNS.items():
            try:
                re.compile(pattern)
            except re.error as e:
                raise AssertionError(f"Pattern {name!r} failed to compile: {e}")

    def test_all_pattern_regions_have_valid_region(self):
        for name, region_key in PATTERN_REGIONS.items():
            assert region_key in SEARCH_REGIONS, (
                f"Pattern {name!r} references unknown region {region_key!r}"
            )

    def test_all_patterns_have_a_region(self):
        for name in PATTERNS:
            assert name in PATTERN_REGIONS, f"Pattern {name!r} has no region mapping"

    def test_siemens_part_pattern_matches_5wk9(self):
        pattern = PATTERNS["siemens_part"]
        assert re.search(pattern, b"5WK90027")
        assert re.search(pattern, b"5WK900271")
        assert re.search(pattern, b"5WK9002710")

    def test_siemens_part_pattern_no_false_positive(self):
        pattern = PATTERNS["siemens_part"]
        assert not re.search(pattern, b"5WK9")  # too few digits
        assert not re.search(pattern, b"5WK90")  # too few digits
        assert not re.search(pattern, b"6WK90027")  # wrong first digit
        assert not re.search(pattern, b"5WS90027")  # wrong prefix

    def test_family_pattern_matches_ms43(self):
        pattern = PATTERNS["family"]
        m = re.search(pattern, b"MS43")
        assert m and m.group() == b"MS43"

    def test_family_pattern_no_false_positive(self):
        pattern = PATTERNS["family"]
        assert not re.search(pattern, b"SID801")
        assert not re.search(pattern, b"MS42")
        assert not re.search(pattern, b"MEDC17")

    def test_calibration_dataset_pattern_matches(self):
        pattern = PATTERNS["calibration_dataset"]
        assert re.search(pattern, b"ca430069.DAT")
        assert re.search(pattern, b"ca43003701.DAT")
        assert re.search(pattern, b"ca4300056010000.DAT")

    def test_calibration_dataset_pattern_no_false_positive(self):
        pattern = PATTERNS["calibration_dataset"]
        assert not re.search(pattern, b"CAPM3630.DAT")
        assert not re.search(pattern, b"ca43006")  # no .DAT
        assert not re.search(pattern, b"cb430069.DAT")  # wrong prefix

    def test_program_number_pattern_matches_bounded_runs(self):
        pattern = PATTERNS["program_number"]
        assert re.search(pattern, b"X430069Y")
        assert re.search(pattern, b"X43003701Y")
        assert re.search(pattern, b"X4300056010000Y")

    def test_program_number_pattern_no_false_positive(self):
        pattern = PATTERNS["program_number"]
        assert not re.search(pattern, b"X1430069Y")  # preceded by a digit
        assert not re.search(pattern, b"MS43")  # no digits after 43
        assert not re.search(pattern, b"X43Y")  # too few digits

    def test_serial_number_pattern_matches(self):
        pattern = PATTERNS["serial_number"]
        m = re.search(pattern, b"5WK90027--1061330037MS43")
        assert m and m.group() == b"1061330037"

    def test_serial_number_pattern_no_false_positive(self):
        pattern = PATTERNS["serial_number"]
        # 12-digit run — a 10-digit window inside it is digit-bounded
        assert not re.search(pattern, b"000000115852")
        assert not re.search(pattern, b"12345")  # too short

    def test_oem_part_number_pattern_matches_standalone(self):
        pattern = PATTERNS["oem_part_number"]
        m = re.search(pattern, b"X7551615Y")
        assert m and m.group() == b"7551615"

    def test_oem_part_number_pattern_no_consecutive_runs(self):
        pattern = PATTERNS["oem_part_number"]
        # Consecutive repeated runs (the real corpus layout) never match
        assert not re.search(pattern, b"7551615755161575")

    def test_ident_record_pattern_matches(self):
        pattern = PATTERNS["ident_record"]
        m = re.search(pattern, IDENT_RECORD)
        assert m and m.group() == b"5WK90027--1061330037MS43"

    def test_ident_record_pattern_no_false_positive(self):
        pattern = PATTERNS["ident_record"]
        assert not re.search(pattern, b"5WK90027")  # no serial/family
        assert not re.search(pattern, b"5WK90027--1061330037SID801")


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_ms43_file_size_is_512kb(self):
        assert MS43_FILE_SIZE == 512 * 1024

    def test_detection_signatures_primary_is_ms43(self):
        assert DETECTION_SIGNATURES[0] == b"MS43"

    def test_detection_signatures_contain_secondary_5wk9(self):
        assert b"5WK9" in DETECTION_SIGNATURES

    def test_exclusion_signatures_not_empty(self):
        assert len(EXCLUSION_SIGNATURES) > 0

    def test_exclusion_signatures_cover_other_siemens_families(self):
        for sig in (b"SID801", b"SID803", b"5WS4", b"PPD", b"SIMOS"):
            assert sig in EXCLUSION_SIGNATURES

    def test_search_regions_have_header(self):
        assert "header" in SEARCH_REGIONS

    def test_search_regions_have_full(self):
        assert "full" in SEARCH_REGIONS

    def test_header_region_is_4kb(self):
        header = SEARCH_REGIONS["header"]
        assert header.start == 0
        assert header.stop == 0x1000

    def test_ident_area_region_is_64kb(self):
        ident = SEARCH_REGIONS["ident_area"]
        assert ident.start == 0
        assert ident.stop == 0x10000

    def test_extended_region_is_128kb(self):
        ext = SEARCH_REGIONS["extended"]
        assert ext.start == 0
        assert ext.stop == 0x20000

    def test_full_region_covers_all(self):
        full = SEARCH_REGIONS["full"]
        assert full.start == 0
        assert full.stop is None

    def test_ident_block_offsets(self):
        assert IDENT_BLOCK == (0x3F80, 0x3FC0)

    def test_identity_offset_constants(self):
        assert SWID_OFFSET == 0x3C34
        assert PROGRAM_NUMBER_OFFSET == 0x6FFBA
        assert SW_NUMBER_TAIL_OFFSET == 0x6FFBF


# ---------------------------------------------------------------------------
# Resolver unit tests — called via extractor internals
# ---------------------------------------------------------------------------


class TestResolverHardwareNumber:
    def test_returns_first_hit(self):
        raw_hits = {"siemens_part": ["5WK90027", "5WK90028"]}
        result = EXTRACTOR._resolve_hardware_number(raw_hits)
        assert result == "5WK90027"

    def test_returns_none_when_no_hits(self):
        raw_hits = {}
        result = EXTRACTOR._resolve_hardware_number(raw_hits)
        assert result is None


class TestResolverSoftwareVersion:
    def test_ca_capture_from_raw_hits(self):
        raw_hits = {"calibration_dataset": ["ca430069.DAT"]}
        result = EXTRACTOR._resolve_software_version(raw_hits, None)
        assert result == "430069"

    def test_fixed_offset_from_data(self):
        buf = make_bin()
        write(buf, 0x6FFBA, b"430069")
        result = EXTRACTOR._resolve_software_version({}, bytes(buf))
        assert result == "430069"

    def test_ca_takes_priority_over_fixed_offset(self):
        buf = make_bin()
        write(buf, 0x70040, b"ca43003701.DAT")
        write(buf, 0x6FFBA, b"430069")
        raw_hits = {"calibration_dataset": ["ca43003701.DAT"]}
        result = EXTRACTOR._resolve_software_version(raw_hits, bytes(buf))
        assert result == "43003701"

    def test_returns_none_when_no_sources(self):
        buf = make_bin()
        result = EXTRACTOR._resolve_software_version({}, bytes(buf))
        assert result is None

    def test_returns_none_when_no_data(self):
        raw_hits = {}
        result = EXTRACTOR._resolve_software_version(raw_hits, None)
        assert result is None


class TestResolverEcuFamily:
    def test_explicit_ms43(self):
        raw_hits = {"family": ["MS43"]}
        result = EXTRACTOR._resolve_ecu_family(raw_hits)
        assert result == "MS43"

    def test_defaults_to_ms43_when_no_family_hit(self):
        raw_hits = {}
        result = EXTRACTOR._resolve_ecu_family(raw_hits)
        assert result == "MS43"

    def test_normalises_to_uppercase(self):
        raw_hits = {"family": ["ms43"]}
        result = EXTRACTOR._resolve_ecu_family(raw_hits)
        assert result == "MS43"


class TestResolverCalibrationId:
    def test_returns_first_hit(self):
        raw_hits = {"calibration_dataset": ["ca430069.DAT"]}
        result = EXTRACTOR._resolve_calibration_id(raw_hits)
        assert result == "ca430069.DAT"

    def test_returns_none_when_no_hits(self):
        raw_hits = {}
        result = EXTRACTOR._resolve_calibration_id(raw_hits)
        assert result is None


# ---------------------------------------------------------------------------
# Registry integration — MS43 appears in the Siemens and global lists
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_ms43_in_siemens_extractors(self):
        from openremap.core.manufacturers.siemens import EXTRACTORS as SIEMENS

        names = [type(e).__name__ for e in SIEMENS]
        assert "SiemensMS43Extractor" in names

    def test_ms43_in_global_extractors(self):
        from openremap.core.manufacturers import EXTRACTORS as GLOBAL

        names = [type(e).__name__ for e in GLOBAL]
        assert "SiemensMS43Extractor" in names

    def test_ms43_after_simtec56_in_registry(self):
        from openremap.core.manufacturers.siemens import EXTRACTORS as SIEMENS

        names = [type(e).__name__ for e in SIEMENS]
        idx_ms43 = names.index("SiemensMS43Extractor")
        idx_simtec = names.index("SiemensSimtec56Extractor")
        assert idx_ms43 > idx_simtec, (
            "MS43 must come after Simtec56 (shared 5WK9 prefix; disjoint "
            "size gates make the order correctness-safe)"
        )

    def test_ms43_before_ems2000_in_registry(self):
        from openremap.core.manufacturers.siemens import EXTRACTORS as SIEMENS

        names = [type(e).__name__ for e in SIEMENS]
        idx_ms43 = names.index("SiemensMS43Extractor")
        idx_ems = names.index("SiemensEMS2000Extractor")
        assert idx_ms43 < idx_ems, (
            "MS43 must come before EMS2000 (EMS2000 detects by exclusion)"
        )

    def test_no_duplicate_extractors(self):
        from openremap.core.manufacturers.siemens import EXTRACTORS as SIEMENS

        names = [type(e).__name__ for e in SIEMENS]
        assert len(names) == len(set(names)), "Duplicate extractors in registry"


# ---------------------------------------------------------------------------
# Corpus-gated integration (skip when tests/data/ is absent)
# ---------------------------------------------------------------------------


class TestCorpusIntegration:
    def test_corpus_identify_base_binary(self):
        path = _corpus_base_file()
        if path is None:
            pytest.skip("MS43 corpus binaries not present")

        from openremap.core.services.identify.identifier import identify_ecu

        data = path.read_bytes()
        assert len(data) == MS43_FILE_SIZE
        ident = identify_ecu(data, path.name)
        assert ident.get("manufacturer") == "Siemens"
        assert ident.get("ecu_family") == "MS43"
        assert ident.get("software_version") == "430069"
        assert ident.get("hardware_number") == "5WK90027"
        assert ident.get("calibration_id") == "ca430069.DAT"
        assert ident.get("serial_number") == "1061330037"
        assert ident.get("match_key") == "MS43::430069"

    def test_corpus_arch_unlock(self):
        """identify_ecu now drives the C166 arch table for MS43."""
        path = _corpus_base_file()
        if path is None:
            pytest.skip("MS43 corpus binaries not present")

        from openremap.core.arch import arch_for_family
        from openremap.core.services.identify.identifier import identify_ecu

        data = path.read_bytes()
        ident = identify_ecu(data, path.name)
        arch = arch_for_family(ident.get("manufacturer"), ident.get("ecu_family"))
        assert arch is not None and arch[0] == "c166"

    def test_corpus_all_files_same_identity(self):
        """Tune-safety check: all 4 corpus files are ONE distinct identity."""
        files = _corpus_files()
        if not files:
            pytest.skip("MS43 corpus binaries not present")

        from openremap.core.services.identify.identifier import identify_ecu

        IDENTITY_FIELDS = (
            "manufacturer",
            "ecu_family",
            "ecu_variant",
            "software_version",
            "hardware_number",
            "calibration_id",
            "calibration_version",
            "sw_base_version",
            "serial_number",
            "dataset_number",
            "oem_part_number",
            "match_key",
            "ident_block",
        )
        identities = []
        for path in files:
            data = path.read_bytes()
            assert len(data) == MS43_FILE_SIZE
            ident = identify_ecu(data, path.name)
            identities.append(tuple(ident.get(f) for f in IDENTITY_FIELDS))

        assert len(identities) >= 1
        first = identities[0]
        for ident in identities[1:]:
            assert ident == first, (
                "All MS43 corpus files must resolve to the same identity "
                "(tune-safety); got a variation across the corpus"
            )
