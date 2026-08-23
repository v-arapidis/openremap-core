"""Tests for openremap.core.services.vin_scanner."""

from __future__ import annotations

import os

from openremap.core.services.identify.vin_scanner import is_valid_check_digit, scan_vins

# A structurally valid VIN: WMI=WAU (Audi), check digit X (verified via
# the ISO 3779 algorithm), year X (1999), numeric tail.
_VALID_VIN = "WAUZZZ8LXX1234567"


class TestCheckDigit:
    def test_known_valid_vin_passes(self):
        assert is_valid_check_digit(_VALID_VIN)

    def test_wrong_digit_fails(self):
        assert not is_valid_check_digit("WAUZZZ8L9X1234567")

    def test_real_opel_vin_check_digit(self):
        # Found in the corpus (Simtec56 Opel Vectra B).  Many EU VINs
        # don't comply with the NA check digit — must NOT be required.
        assert not is_valid_check_digit("W0L0JBF19W5117067")

    def test_short_or_long_rejected(self):
        assert not is_valid_check_digit("WAUZZZ8LX1234567")
        assert not is_valid_check_digit("WAUZZZ8LXX12345678")

    def test_illegal_chars_rejected(self):
        assert not is_valid_check_digit("WAUZZZ8LXX123456I")


class TestScanVins:
    def _bin(self, vin: str, *, ident: bool = True, mirrors: int = 1) -> bytes:
        blob = bytearray(os.urandom(0x400))
        if ident:
            ident_block = b"IDENT-METADATA-BLOCK  " + vin.encode() + b"  " * 40
            blob[0x100 : 0x100 + len(ident_block)] = ident_block
            offs = [0x100 + ident_block.find(vin.encode())]
        else:
            # place the VIN alone in "code" (no ASCII block around it)
            offs = [0x280]
            blob[0x280 : 0x280 + 17] = vin.encode()
        p = offs[0] + 0x30 + 17
        for _ in range(mirrors - 1):
            blob[p : p + 17] = vin.encode()
            p += 0x30
        return bytes(blob)

    def test_valid_vin_in_ident_block_scores_high(self):
        data = self._bin(_VALID_VIN)
        hits = scan_vins(data)
        assert hits
        top = hits[0]
        assert top.vin == _VALID_VIN
        assert top.confidence >= 0.8
        assert top.wmi_known and top.check_digit_ok and top.in_ident_block

    def test_vin_in_code_scores_lower(self):
        data = self._bin(_VALID_VIN, ident=False)
        top = scan_vins(data)[0]
        assert top.confidence < 0.8
        assert not top.in_ident_block

    def test_mirror_consensus_adds_evidence(self):
        single = self._bin(_VALID_VIN, mirrors=1)[0] if False else scan_vins(
            self._bin(_VALID_VIN, mirrors=1)
        )[0]
        multi = scan_vins(self._bin(_VALID_VIN, mirrors=3))[0]
        assert multi.mirror_count == 3
        assert multi.confidence > single.confidence

    def test_pattern_fills_are_rejected_entirely(self):
        blob = bytearray(os.urandom(0x400))
        blob[0x100 : 0x100 + 17] = b"9" * 17
        assert scan_vins(bytes(blob)) == []

    def test_illegal_ioq_rejected(self):
        blob = bytearray(os.urandom(0x400))
        blob[0x100 : 0x100 + 17] = b"I" * 17
        assert scan_vins(bytes(blob)) == []

    def test_lookalike_serial_scores_low(self):
        # A real corpus lookalike: calibration/serial pattern, no WMI.
        blob = bytearray(os.urandom(0x400))
        blob[0x100 : 0x100 + 17] = b"1037541778126241V"
        hits = scan_vins(bytes(blob))
        assert all(h.confidence < 0.6 for h in hits)

    def test_min_confidence_filter(self):
        # ident + single copy → 0.85; mirrored → 0.95 (cap)
        data = self._bin(_VALID_VIN)
        assert scan_vins(data, min_confidence=0.8)
        assert scan_vins(data, min_confidence=0.9) == []
        assert scan_vins(self._bin(_VALID_VIN, mirrors=3), min_confidence=0.95)

    def test_vin_after_aligned_alnum_run_is_found(self):
        # Regression (2026-08-20): the old non-overlapping finditer
        # stride skipped a VIN whenever preceding [A-Z0-9] text left a
        # matching 17-char window that consumed into the VIN start.
        blob = bytearray(os.urandom(0x400))
        run = b"CALIBRATIONDATABLOCK" + _VALID_VIN.encode()
        blob[0x100 : 0x100 + len(run)] = run
        hits = scan_vins(bytes(blob))
        assert any(h.vin == _VALID_VIN for h in hits)

    def test_empty_data(self):
        assert scan_vins(b"") == []


class TestRealCorpus:
    def test_opel_vin_found_in_simtec56_bin(self):
        from pathlib import Path

        rel = Path(__file__).parent.parent.parent / "data" / "ECUs" / "Siemens" / "Simtec56" / (
            "Opel Vectra B 1.8i 115HP 5WK9073 GM90506365__1__1.ori"
        )
        if not rel.exists():
            import pytest

            pytest.skip("corpus binary not present")
        hits = scan_vins(rel.read_bytes(), min_confidence=0.4)
        assert any(h.vin == "W0L0JBF19W5117067" for h in hits)

    def test_natural_corpus_has_no_false_positives_above_0_6(self):
        import json
        from pathlib import Path

        import pytest

        repo = Path(__file__).parent.parent.parent.parent  # tests/…/services → repo root
        manifest = repo / "tests" / "data" / "synthetic-tunes" / "manifest.json"
        if not manifest.exists():
            pytest.skip("synthetic-tunes corpus not generated")
        stocks = {
            t["stock"] for t in json.loads(manifest.read_text())["tunes"]
        }
        for rel in sorted(stocks):
            hits = scan_vins((repo / rel).read_bytes())
            strong = [h for h in hits if h.confidence >= 0.6]
            assert not strong, (
                f"false positive in {rel}: "
                + ", ".join(f"{h.vin}({h.confidence})" for h in strong)
            )
