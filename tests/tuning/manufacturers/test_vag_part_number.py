"""Tests for the shared VAG part-number scanner (bosch/_vag.py)."""

import pytest

from openremap.core.manufacturers.bosch._vag import find_vag_part_number


class TestFindVagPartNumber:
    def test_standard_letter_position_three(self):
        assert find_vag_part_number(b"xx 03G906016AC xx") == "03G906016AC"

    def test_digit_position_three(self):
        assert find_vag_part_number(b"xx 038906016L xx") == "038906016L"

    def test_engine_code_prefix(self):
        assert find_vag_part_number(b"xx 06A906032LP xx") == "06A906032LP"

    def test_platform_prefix_4b(self):
        assert find_vag_part_number(b"xx 4B0906018DF xx") == "4B0906018DF"

    def test_platform_prefix_3b_no_trailing_letter(self):
        assert find_vag_part_number(b"xx 3B0907401 xx") == "3B0907401"

    def test_spaced_format_normalized(self):
        assert find_vag_part_number(b"xx 03L 906 018 AJ xx") == "03L906018AJ"

    def test_rejects_digit_only_hw(self):
        assert find_vag_part_number(b"xx 0261200352 xx") is None

    def test_rejects_digit_only_sw(self):
        assert find_vag_part_number(b"xx 1037370476 xx") is None

    def test_rejects_digit_only_calibration_code(self):
        assert find_vag_part_number(b"xx 035659997 xx") is None
        assert find_vag_part_number(b"xx 037906259 xx") is None

    def test_skips_earlier_digit_only_code(self):
        """A cal code of the same shape must not end the search.

        Regression: ``search()`` + a post-hoc letter guard reported None for a
        dump whose real part number simply sat later in the file.
        """
        data = b"xx 037906259 xx" + b"\x00" * 40 + b"xx 038906019CJ xx"
        assert find_vag_part_number(data) == "038906019CJ"

    def test_skips_several_digit_only_codes(self):
        data = (
            b"xx 035659997 xx"
            + b"\x00" * 8
            + b"xx 1037370476 xx"
            + b"\x00" * 8
            + b"xx 03G906016AC xx"
        )
        assert find_vag_part_number(data) == "03G906016AC"

    def test_skips_earlier_digit_only_code_spaced_form(self):
        data = b"xx 037906259 xx" + b"\x00" * 16 + b"xx 03L 906 018 AJ xx"
        assert find_vag_part_number(data) == "03L906018AJ"

    def test_order_independent_for_digits_plus_one_part_number(self):
        pn = b"xx 038906019CJ xx"
        code = b"xx 037906259 xx"
        assert find_vag_part_number(code + b"\x00" * 16 + pn) == "038906019CJ"
        assert find_vag_part_number(pn + b"\x00" * 16 + code) == "038906019CJ"

    def test_all_digit_codes_only_still_none(self):
        data = b"xx 037906259 xx" + b"\x00" * 16 + b"xx 1037370476 xx"
        assert find_vag_part_number(data) is None

    def test_returns_none_on_no_match(self):
        assert find_vag_part_number(b"no part numbers here") is None
        assert find_vag_part_number(b"") is None
