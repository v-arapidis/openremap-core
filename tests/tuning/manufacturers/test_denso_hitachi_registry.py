"""
Integration tests for the Denso and Hitachi extractor packages.

Covers:
  - Registry: both packages registered, ordered after the European
    manufacturers, Denso before Hitachi
  - Whole-corpus classification (skip-guarded):
      * every Subaru corpus file is claimed by exactly one extractor
        (no contested files, no unclaimed files)
      * every claimed Subaru file lands on Denso or Hitachi — no other
        manufacturer claims a Subaru ROM
      * no Bosch / Siemens / Unknown corpus file is claimed by Denso or
        Hitachi (no cross-contamination)
"""

import glob
from pathlib import Path

import pytest

from openremap.core.manufacturers import BUILTIN_EXTRACTORS, get_extractors
from openremap.core.manufacturers.denso import EXTRACTORS as DENSO_EXTRACTORS
from openremap.core.manufacturers.hitachi import EXTRACTORS as HITACHI_EXTRACTORS

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_SUBARU = (DATA / "ECUs" / "Subaru").is_dir()


def _subaru_files() -> list[str]:
    return sorted(glob.glob(str(DATA / "ECUs" / "Subaru" / "**" / "*.hex"), recursive=True))


def _other_corpora_files() -> list[str]:
    out = []
    for root in ("Bosch", "Siemens", "Unknown"):
        base = DATA / "ECUs" / root
        if not base.is_dir():
            continue
        out.extend(
            f for f in glob.glob(str(base / "**" / "*"), recursive=True) if Path(f).is_file()
        )
    return out


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_builtin_registry_contains_denso_and_hitachi(self):
        names = [type(e).__name__ for e in BUILTIN_EXTRACTORS]
        for cls in (DENSO_EXTRACTORS + HITACHI_EXTRACTORS):
            assert type(cls).__name__ in names

    def test_denso_ordered_after_european_manufacturers(self):
        names = [type(e).__name__ for e in BUILTIN_EXTRACTORS]
        marelli_last = max(
            i for i, n in enumerate(names) if n.startswith("Marelli")
        )
        denso_first = min(
            i for i, n in enumerate(names) if n.startswith("Denso")
        )
        assert denso_first > marelli_last

    def test_denso_before_hitachi(self):
        names = [type(e).__name__ for e in BUILTIN_EXTRACTORS]
        denso_last = max(i for i, n in enumerate(names) if n.startswith("Denso"))
        hitachi_first = min(
            i for i, n in enumerate(names) if n.startswith("Hitachi")
        )
        assert denso_last < hitachi_first

    def test_denso_intra_brand_order(self):
        names = [type(e).__name__ for e in DENSO_EXTRACTORS]
        assert names == [
            "DensoSH7055Extractor",
            "DensoSH7058Extractor",
            "DensoDieselExtractor",
            "DensoSH72531Extractor",
        ]

    def test_hitachi_intra_brand_order(self):
        names = [type(e).__name__ for e in HITACHI_EXTRACTORS]
        assert names == ["HitachiSH72546Extractor"]

    def test_get_extractors_returns_full_list(self):
        assert len(get_extractors()) == len(BUILTIN_EXTRACTORS)


# ---------------------------------------------------------------------------
# Whole-corpus classification (skip-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SUBARU, reason="tests/data/ECUs/Subaru corpus missing")
class TestCorpusClassification:
    def test_every_subaru_file_claimed_exactly_once(self):
        for f in _subaru_files():
            data = Path(f).read_bytes()
            hits = [e for e in BUILTIN_EXTRACTORS if e.can_handle(data)]
            assert len(hits) == 1, f"{Path(f).name}: {[type(e).__name__ for e in hits]}"

    def test_subaru_files_only_claimed_by_denso_or_hitachi(self):
        for f in _subaru_files():
            data = Path(f).read_bytes()
            hits = [e for e in BUILTIN_EXTRACTORS if e.can_handle(data)]
            assert hits, f"{Path(f).name}: unclaimed"
            assert hits[0].name in ("Denso", "Hitachi"), (
                f"{Path(f).name}: claimed by {hits[0].name}"
            )

    def test_no_cross_contamination_on_other_corpora(self):
        denso_hitachi = [e for e in BUILTIN_EXTRACTORS if e.name in ("Denso", "Hitachi")]
        for f in _other_corpora_files():
            data = Path(f).read_bytes()
            bad = [type(e).__name__ for e in denso_hitachi if e.can_handle(data)]
            assert not bad, f"{Path(f).name}: claimed by {bad}"

    def test_every_subaru_file_extracts_a_version(self):
        for f in _subaru_files():
            data = Path(f).read_bytes()
            extractor = next(e for e in BUILTIN_EXTRACTORS if e.can_handle(data))
            result = extractor.extract(data, Path(f).name)
            assert result["software_version"], f"{Path(f).name}: no software_version"
            assert result["match_key"], f"{Path(f).name}: no match_key"
