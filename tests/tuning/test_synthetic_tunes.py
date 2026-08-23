"""
Integrity tests for the synthetic-tunes corpus.

The corpus is GENERATED (benchmarks/make_synthetic_tunes.py) — these
tests verify the artifacts instead of regenerating them.  Every test
skips when the corpus is absent (it is gitignored).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MANIFEST = ROOT / "tests" / "data" / "synthetic-tunes" / "manifest.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(), reason="synthetic-tunes corpus not generated"
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


class TestCorpusIntegrity:
    def test_manifest_has_expected_scale(self, manifest):
        tunes = manifest["tunes"]
        assert len(tunes) >= 200, "expected 5 variants for 40+ families"
        families = {t["family"] for t in tunes}
        assert len(families) >= 40

    def test_every_tune_file_exists_with_matching_hash(self, manifest):
        for t in manifest["tunes"]:
            path = ROOT / t["tuned"]
            assert path.exists(), f"missing {t['tuned']}"
            data = path.read_bytes()
            assert len(data) == t["size"], f"size mismatch {t['tuned']}"
            assert hashlib.sha256(data).hexdigest() == t["tuned_sha256"]

    def test_stocks_exist_and_size_matches(self, manifest):
        for t in manifest["tunes"]:
            stock = ROOT / t["stock"]
            assert stock.exists(), f"missing stock {t['stock']}"
            assert stock.stat().st_size == t["size"], (
                f"tuned size differs from stock for {t['stock']}"
            )

    def test_every_variant_actually_changed_cells(self, manifest):
        for t in manifest["tunes"]:
            assert t["cells_changed"] > 0, f"{t['tuned']} changed nothing"
            assert t["maps_touched"] >= 1

    def test_variants_of_same_family_differ(self, manifest):
        by_stock: dict[str, list[str]] = {}
        for t in manifest["tunes"]:
            by_stock.setdefault(t["stock"], []).append(t["tuned_sha256"])
        for stock, hashes in by_stock.items():
            assert len(set(hashes)) == len(hashes), (
                f"variants of {stock} are not distinct"
            )


class TestCorpusRealism:
    def test_edits_land_in_calibration_regions(self, manifest):
        """Spot-check: re-scan one small stock and verify every edited
        map offset sits inside a calibration region."""
        from openremap.core.services.maps.layout import segment
        from openremap.core.services.maps.map_hunter import scan_map_tables

        t = next(x for x in manifest["tunes"] if x["family"] == "Bosch/EDC1")
        stock = (ROOT / t["stock"]).read_bytes()
        tables = scan_map_tables(stock, min_score=0.55, max_series_tables=16)
        regions = segment(stock, tables=tables)
        cal_ranges = [
            (r.start, r.end) for r in regions if r.kind == "calibration"
        ]
        for m in t["maps"]:
            assert any(s <= m["offset"] < e for s, e in cal_ranges), (
                f"map 0x{m['offset']:X} outside calibration"
            )

    def test_tuned_still_identifies_as_same_family(self, manifest):
        from openremap.core.services.identify.identifier import identify_ecu

        t = next(x for x in manifest["tunes"] if x["family"] == "Bosch/EDC1")
        stock_id = identify_ecu(
            data=(ROOT / t["stock"]).read_bytes(), filename="stock.bin"
        )
        tuned_id = identify_ecu(
            data=(ROOT / t["tuned"]).read_bytes(), filename="tuned.bin"
        )
        assert tuned_id.get("match_key") == stock_id.get("match_key")

    def test_cook_round_trip_on_small_family(self, manifest):
        from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer

        t = next(x for x in manifest["tunes"] if x["family"] == "Bosch/EDC1")
        stock = (ROOT / t["stock"]).read_bytes()
        tuned = (ROOT / t["tuned"]).read_bytes()
        analyzer = ECUDiffAnalyzer(
            original_data=stock,
            modified_data=tuned,
            original_filename="stock.bin",
            modified_filename="tuned.bin",
        )
        recipe = analyzer.build_recipe()
        assert len(recipe["instructions"]) >= 1
        # every instruction must fall inside one of the edited maps' byte
        # spans (data cells — map offset + strided rows)
        spans = []
        for m in t["maps"]:
            row_bytes = m["cols"] * m["cell_width"]
            stride = m["stride"] if m["stride"] is not None else row_bytes
            for r in range(m["rows"]):
                spans.append(
                    (m["offset"] + r * stride, m["offset"] + r * stride + row_bytes)
                )
        for inst in recipe["instructions"]:
            off = inst["offset"]
            assert any(s <= off < e for s, e in spans), (
                f"instruction at 0x{off:X} outside the edited maps"
            )


class TestGeneratorDeterminism:
    def test_regeneration_is_byte_identical(self, manifest):
        """Re-running the generator's edit logic on the same seed must
        reproduce the stored file byte-for-byte."""
        import sys

        sys.path.insert(0, str(ROOT / "benchmarks"))
        import make_synthetic_tunes as gen  # type: ignore[import-not-found]

        t = next(
            x for x in manifest["tunes"]
            if x["family"] == "Bosch/EDC1" and x["variant"] == 1
        )
        stock = (ROOT / t["stock"]).read_bytes()

        tables = gen.scan_map_tables(stock, min_score=0.55, max_series_tables=16)
        regions = gen.segment(stock, tables=tables)
        cal_tables = [
            tb
            for tb in tables
            if tb.score >= 0.85
            and any(
                r.kind == "calibration" and r.start <= tb.offset < r.end
                for r in regions
            )
        ]
        top = sorted(cal_tables, key=lambda tb: tb.score, reverse=True)[
            : gen._CANDIDATE_POOL
        ]
        import random

        rng = random.Random(gen.SEED_BASE * 100 + 1)
        chosen = rng.sample(top, min(gen._MAPS_PER_VARIANT, len(top)))
        buf = bytearray(stock)
        for tb in chosen:
            gen._edit_map(buf, stock, tb, rng)

        assert hashlib.sha256(buf).hexdigest() == t["tuned_sha256"]
