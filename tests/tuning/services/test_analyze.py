"""
Unit tests for ``openremap.core.services.analyze`` — the unified
``analyze_binary`` composition.  Deterministic synthetic inputs only.
"""

import random

from openremap.core.services.analyze import analyze_binary

from tests.conftest import make_layout_bin

# ISO-3779-valid Audi VIN (WAU in the scanner whitelist, check digit X).
_VALID_VIN = "WAUZZZ8LXX1234567"


def _bin_with_vin(vin: str) -> bytes:
    blob = bytearray(random.Random(7).randbytes(0x400))
    ident = b"IDENT-METADATA-BLOCK  " + vin.encode() + b"  " * 40
    blob[0x100 : 0x100 + len(ident)] = ident
    return bytes(blob)


class TestAnalyzeBinary:
    def test_zero_filled_runs_and_reports_unknown(self):
        report = analyze_binary(b"\x00" * 4096, "zero.bin")
        assert report.identity.get("ecu_family") is None
        assert report.confidence.tier in ("Low", "Suspicious", "Unknown")
        assert report.file_size == 4096
        assert len(report.sha256) == 64

    def test_layout_bin_finds_regions_and_tables(self):
        report = analyze_binary(make_layout_bin(), "layout.bin")
        kinds = {r.kind for r in report.regions}
        assert "calibration" in kinds
        assert "code" in kinds
        assert report.axis_count > 0
        assert report.tables  # the real map in the calibration sector

    def test_fast_skips_maps_checksums_health(self):
        report = analyze_binary(make_layout_bin(), "layout.bin", fast=True)
        assert report.fast is True
        assert report.tables == []
        assert report.axis_count == 0
        assert report.checksums is None
        assert report.health is None
        # identity is still there
        assert report.identity is not None

    def test_skip_maps_keeps_checksums(self):
        report = analyze_binary(make_layout_bin(), "layout.bin", skip_maps=True)
        assert report.tables == []
        assert report.checksums is not None
        assert report.health is not None

    def test_vin_candidate_decoded(self):
        report = analyze_binary(_bin_with_vin(_VALID_VIN), "vin.bin")
        assert report.vin is not None
        assert report.vin.decoded is True
        assert report.vin.manufacturer == "Audi"
        assert report.vin_confidence is not None
        assert report.vin_confidence >= 0.6

    def test_to_dict_is_json_serialisable(self):
        import json

        report = analyze_binary(make_layout_bin(), "layout.bin")
        blob = json.dumps(report.to_dict())
        d = json.loads(blob)
        for key in (
            "container", "file_size", "sha256", "identity", "confidence",
            "vin", "hardware", "layout", "maps", "checksums", "health", "fast",
        ):
            assert key in d
