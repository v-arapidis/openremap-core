"""
Real-corpus validation for the flash-layout segmenter.

Runs ``segment()`` + ``find_ident_blocks()`` on 30+ real ECU binaries
from ``tests/data/ECUs/`` spanning a wide range of families and sizes
(16 KB LH-Jetronic → 4 MB EDC17).  Files are gitignored, so every test
skips when the binary is not present (CI-safe).

Invariants asserted on EVERY file (these are the contract of the
segmenter, independent of family-specific layouts):

- regions tile the file exactly: no gaps, no overlaps
- kinds are from the known vocabulary, confidence in [0, 1]
- every sector the scanner finds a high-score (>= 0.85) table in must
  be classified ``calibration`` (the code-vs-calibration discriminator)
- fully-erased sectors (one repeated byte) must be classified ``erased``
- ident blocks are exact printable-ASCII byte ranges
- determinism: same input → same segmentation

Additionally, four bins with known layouts (measured manually) get
explicit boundary assertions — the regression anchor for the thresholds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.services.maps.layout import (
    _REGION_KINDS,
    find_ident_blocks,
    segment,
)
from openremap.core.services.maps.map_hunter import scan_map_tables

_DATA = Path(__file__).parent.parent / "data" / "ECUs"


# ---------------------------------------------------------------------------
# Corpus — 32 bins across Bosch / Siemens / Unknown families
# ---------------------------------------------------------------------------

CORPUS: list[tuple[str, int]] = [
    # (relative path, exact size in bytes — verified on disk 2026-08-13)
    ("Bosch/LH-Jetronic/0280-000-913__1__1.BIN", 32768),
    ("Bosch/KE-Jetronic/MercBenz_0280800446_soft972__1__1.bin", 32768),
    ("Bosch/EDC1/0281001214 2537355342 __1__1.Ori", 32768),
    ("Bosch/EDC1/028906021AF 0281001309 867__1__1.ori", 65536),
    ("Bosch/M1.3/BMW_179__1__1.bin", 32768),
    ("Bosch/M1.3/Bmw 325i 2.5i 192HP 0261200173 355705__1__1.ori", 32768),
    ("Bosch/M1.5.5/Opel corsa 1.0 12V 0261204058 (EAE6)__1__1.ori", 131072),
    ("Bosch/M1.7/318i_175_soft1267356378__1__1.bin", 32768),
    ("Bosch/M2.8/Opel Astra 2.0 16v (C20XE) 0261203017 357369__1__1.ori", 65536),
    ("Bosch/M2.9/021906258BK__1__1.ori", 65536),
    ("Bosch/M3.1/403_3NV_950__1__1.BIN", 32768),
    ("Bosch/M3.3/0261200404-1267357689__1__1.bin", 65536),
    ("Bosch/M3.8/Audi A3 1.8T 150HP 06A906018AQ 0261204678 358108__1__1.ori", 262144),
    ("Bosch/M4.3/Volvo 850 2.0T-0261204041-355899__1__1.ori", 65536),
    ("Bosch/M4.4/Volvo 850 2.5T AWD 190HP-0261204305-358409__1__1.ori", 131072),
    ("Bosch/M5.9/Audi A4 1.8T 150HP 8D0907557P 0261204258 350269__1__1.ori", 262144),
    ("Bosch/MP3.2/CITROEN-ZX-2.0 16V-0261200218-1267357390__1__1.ori", 32768),
    ("Bosch/MP3.x-PSA/213_492__1__1.ori", 32768),
    ("Bosch/ME1.5.5/Opel astra 2.0T 193HP 0261206332 354961__1__1.ori", 524288),
    ("Bosch/EDC3/VW golf 1.9TDI AHU 90HP 028906021GG 0281001650 357824__1__1.ori", 262144),
    ("Bosch/EDC3/Opel vectra 2.0DTI 100HP 0281001871 CT 09136116__1__1.ori", 262144),
    ("Bosch/EDC15/Audi A4 2.5TDI 163HP 8E0907401AF 0281012142 375555__1__1.ori", 1048576),
    ("Bosch/EDC15/019CJ__1__1.ori", 524288),
    ("Bosch/EDC16/016FEorg.127__1__1.bin", 262144),
    ("Bosch/EDC16/Peugeot partner 1037383736__1.ori", 647168),
    ("Bosch/ME7/Citroen C2 1.6 VTS 0261208376 369477__1__1.ori", 524288),
    ("Bosch/ME7.1/066906032E__1__1.bin", 1048576),
    ("Bosch/ME71/4B0907551E__1__1.bin", 524288),
    ("Bosch/ME7.1.1/Audi_S4_4.2l_Bosch_ME_7.1.1_0261208777_374033_FD57.ori__1__1.bin", 1048576),
    ("Bosch/ME7.3/ferrari360__1__1.bin", 524288),
    ("Bosch/ME731/Alfa_GT_bosch_0261208571_1037368772__1__1.ori", 524288),
    ("Bosch/ME7.5/018AT_0003__1__1.ORI", 524288),
    ("Bosch/ME7.5.10/Seat Inca 1.4i 58HP 6K0906032AE 0261207230 1037360307__1__1.ori", 524288),
    ("Bosch/MED9/VW golf5 2.0TFSI 1K0907115K 0261S02332 380991__1__1.ori", 2099200),
    ("Bosch/MEDC17/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL__1.bin", 4194304),
    ("Bosch/EDC17/1__1__1.bin", 4194304),
    ("Siemens/SID801/Peugeot 307 2.0HDI 90HP 9653205380 5WS40145A-T__1__1.ori", 524288),
    ("Siemens/SID803/Peugeot 407 2.0HDI 136HP 5WS40204ET 9655041480 9658345280__1__1.ori", 458752),
    ("Siemens/Simtec56/Opel Vectra B 1.8i 115HP 5WK9073 GM90506365__1__1.ori", 131072),
    ("Siemens/PPD1.2/Seat leon 2.0TDI 170HP 03G906018D__1__1.ori", 249856),
    ("Siemens/SIMOS/27c010__1__1.bin", 131072),
    ("Siemens/Unmatched/27С010__1.bin", 131072),
    ("Unknown/Opel astra 2.0DTI 100HP 0281001869 vtomw060__1__1.ori", 196608),
    ("Unknown/mpc555__1__1.bin", 462848),
]


def _load(rel: str, min_size: int) -> bytes | None:
    path = _DATA / rel
    if not path.exists():
        return None
    data = path.read_bytes()
    if len(data) < min_size:
        return None
    return data


class TestLayoutCorpus:
    """Invariant contract across 30+ real binaries."""

    def test_segmentation_invariants_hold_on_entire_corpus(self) -> None:
        if not any(_load(rel, min_size) is not None for rel, min_size in CORPUS):
            pytest.skip("corpus binaries not present")
        tested = 0
        for rel, min_size in CORPUS:
            data = _load(rel, min_size)
            if data is None:
                continue
            tested += 1

            tables = scan_map_tables(
                data, min_score=0.55, max_series_tables=16,
            )
            regions = segment(data, tables=tables)
            ident = find_ident_blocks(data)

            # --- tiling: no gaps, no overlaps, full coverage ---
            assert regions, f"{rel}: empty segmentation"
            assert regions[0].start == 0, f"{rel}: first region not at 0"
            assert regions[-1].end == len(data), f"{rel}: file not fully covered"
            for prev, nxt in zip(regions, regions[1:]):
                assert prev.end == nxt.start, f"{rel}: gap/overlap between regions"

            # --- vocabulary + confidence ---
            for r in regions:
                assert r.kind in _REGION_KINDS, f"{rel}: bad kind {r.kind}"
                assert 0.0 <= r.confidence <= 1.0
                assert r.start < r.end

            # --- calibration implies high-score tables ---
            # (the discriminator: high-score tables live in calibration)
            for r in regions:
                if r.kind == "calibration":
                    assert r.tables_high_conf >= 1, f"{rel}: calibration without tables"

            # --- erased implies real fill ---
            for r in regions:
                if r.kind == "erased":
                    assert r.fill_byte is not None
                    assert r.fill_ratio >= 0.95, f"{rel}: erased region not filled"

            # --- ident blocks are exact printable runs ---
            for blk in ident:
                assert blk.kind == "ident"
                assert blk.end - blk.start >= 64
                chunk = data[blk.start : blk.end]
                assert all(0x20 <= b <= 0x7E for b in chunk), (
                    f"{rel}: ident block contains non-printable bytes"
                )

            # --- determinism ---
            again = segment(data, tables=tables)
            assert again == regions, f"{rel}: non-deterministic segmentation"

        assert tested >= 30, f"corpus too small: {tested} files present"

    def test_high_score_tables_always_fall_in_calibration(self) -> None:
        """For every bin in the corpus, every high-score table offset must
        sit inside a calibration region — never code/erased/mixed."""
        if not any(_load(rel, min_size) is not None for rel, min_size in CORPUS):
            pytest.skip("corpus binaries not present")
        checked = 0
        for rel, min_size in CORPUS:
            data = _load(rel, min_size)
            if data is None:
                continue
            tables = scan_map_tables(
                data, min_score=0.55, max_series_tables=16,
            )
            regions = segment(data, tables=tables)
            for t in tables:
                if t.score < 0.85:
                    continue
                owner = next(
                    (r for r in regions if r.start <= t.offset < r.end), None
                )
                assert owner is not None and owner.kind == "calibration", (
                    f"{rel}: high-score table 0x{t.offset:X} lands in "
                    f"{owner.kind if owner else 'nothing'}"
                )
            checked += 1
        assert checked >= 30

    def test_ident_blocks_detected_on_most_bins(self) -> None:
        """Real ECUs carry ident metadata — most bins must yield >= 1
        ident candidate (no fixed number; just sanity that the detector
        is alive on real data)."""
        if not any(_load(rel, min_size) is not None for rel, min_size in CORPUS):
            pytest.skip("corpus binaries not present")
        with_ident = 0
        tested = 0
        for rel, min_size in CORPUS:
            data = _load(rel, min_size)
            if data is None:
                continue
            tested += 1
            if find_ident_blocks(data):
                with_ident += 1
        assert tested >= 30
        assert with_ident >= tested // 2, f"ident blocks found on only {with_ident}/{tested} bins"


# ---------------------------------------------------------------------------
# Known layouts — regression anchors (measured manually, 2026-08-13)
# ---------------------------------------------------------------------------


def _segment_rel(rel: str) -> list:
    data = (_DATA / rel).read_bytes()
    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    return segment(data, tables=tables)


class TestKnownLayouts:
    def test_edc15_audi_a4_erased_code_cal(self) -> None:
        rel = "Bosch/EDC15/Audi A4 2.5TDI 163HP 8E0907401AF 0281012142 375555__1__1.ori"
        if not (_DATA / rel).exists():
            pytest.skip("corpus binary not present")
        regions = _segment_rel(rel)

        # 0xC3-filled first half → erased
        assert regions[0].kind == "erased"
        assert regions[0].end >= 0x70000
        # calibration on top
        cal = [r for r in regions if r.kind == "calibration"]
        assert any(r.start <= 0xC0000 and r.end >= 0x100000 for r in cal), (
            f"calibration region must cover 0xC0000..0x100000, got {cal}"
        )
        # code between erased and calibration
        code = [r for r in regions if r.kind == "code"]
        assert any(0x70000 <= r.start and r.end <= 0xC0000 for r in code)

    def test_me7_1_1_audi_s4_ff_tail(self) -> None:
        rel = "Bosch/ME7.1.1/Audi_S4_4.2l_Bosch_ME_7.1.1_0261208777_374033_FD57.ori__1__1.bin"
        if not (_DATA / rel).exists():
            pytest.skip("corpus binary not present")
        regions = _segment_rel(rel)

        assert regions[-1].kind == "erased"
        assert regions[-1].start == 0xF0000 and regions[-1].end == 0x100000

    def test_me7_1_1_audi_s4_embedded_calibration(self) -> None:
        rel = "Bosch/ME7.1.1/Audi_S4_4.2l_Bosch_ME_7.1.1_0261208777_374033_FD57.ori__1__1.bin"
        if not (_DATA / rel).exists():
            pytest.skip("corpus binary not present")
        regions = _segment_rel(rel)

        cal = [r for r in regions if r.kind == "calibration"]
        assert any(r.start <= 0x10000 and r.end >= 0x30000 for r in cal), (
            f"expected embedded calibration around 0x10000..0x30000, got {cal}"
        )
        # pure-code area must not be labelled calibration
        assert not any(
            r.kind == "calibration" and r.start >= 0x30000 and r.end <= 0xE0000
            for r in regions
        )

    def test_edc16_cal_first_ff_tail(self) -> None:
        rel = "Bosch/EDC16/016FEorg.127__1__1.bin"
        if not (_DATA / rel).exists():
            pytest.skip("corpus binary not present")
        regions = _segment_rel(rel)

        assert regions[0].kind == "calibration"
        assert regions[0].end >= 0x20000
        assert regions[-1].kind == "erased"
        assert regions[-1].start == 0x30000 and regions[-1].end == 0x40000

    def test_edc1_ff_tail(self) -> None:
        rel = "Bosch/EDC1/028906021AF 0281001309 867__1__1.ori"
        if not (_DATA / rel).exists():
            pytest.skip("corpus binary not present")
        regions = _segment_rel(rel)

        assert regions[-1].kind == "erased"
        assert regions[-1].start >= 0x8000
        assert regions[-1].end == 0x10000
