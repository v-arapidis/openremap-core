"""
Unit tests for ``openremap.core.services.vin_decode`` — vininfo-based VIN
decoding.  Deterministic inputs only (vininfo's own README examples and
corpus-proven VINs) — no files, no corpus.
"""

import pytest

from openremap.core.services.vin_decode import decode_vin


class TestDecodeVIN:
    def test_known_vin_decodes(self):
        """Renault example from vininfo's README — deterministic."""
        d = decode_vin("VF1LM1B0H36666155")
        assert d.decoded is True
        assert d.manufacturer == "Renault"
        assert d.region == "Europe"
        assert d.country == "France"

    def test_corpus_vw_vin_decodes(self):
        """The Golf 5 VIN found in the real MED9 corpus."""
        d = decode_vin("WVWZZZ1KZ7W059972")
        assert d.decoded is True
        assert d.manufacturer == "Volkswagen"
        assert d.country == "Germany"
        assert 2007 in d.years

    def test_malformed_short_never_raises(self):
        d = decode_vin("ABC")
        assert d.decoded is False
        assert d.manufacturer is None

    def test_illegal_chars_never_raises(self):
        d = decode_vin("IOQIOQIOQIOQIOQ12")
        assert d.decoded is False

    def test_unknown_wmi_not_decoded(self):
        """vininfo marks unknown WMIs 'UnsupportedBrand' — we treat as no info."""
        d = decode_vin("ZZZZZZZZZZZZZZZZZ")
        assert d.decoded is False
        assert d.manufacturer is None
        assert d.country is None  # no vininfo guesswork for unknown WMIs

    def test_empty_string_never_raises(self):
        assert decode_vin("").decoded is False

    def test_checksum_reported_when_computable(self):
        # All-zeros VIN is structurally parseable; checksum is mechanical.
        d = decode_vin("12345678901234567")
        assert isinstance(d.checksum_valid, bool)
