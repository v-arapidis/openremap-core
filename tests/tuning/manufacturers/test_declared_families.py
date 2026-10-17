"""
Guard: a declared family key must be one the engine can actually report.

`supported_families` is not documentation-only. The TUI families browser lists
every entry (`tui/app.py`), and the contribution wizard will read the list to
decide which files a candidate signature must not collide with. It is also,
historically, **over-declared**: 15 of the 104 keys cannot be returned by any
code path — most because a resolver deliberately normalises them onto a parent
family (`M3.8.3` → `M3.8`, `M5.92` → `M5.9`, `M2.81` → `M2.8`), the rest because
the family is hardcoded (`EDC2` → `EDC1`, every `EDC15C*` → `EDC15`) or is only
a detection marker (`ME7910` → `ME7.9.10`).

This test pins that in both directions:

* a **newly declared** key must either have a fixture in the wizard corpus or be
  added to ``KNOWN_UNREPORTABLE`` with a reason — over-declaring cannot happen
  silently again;
* a key listed in ``KNOWN_UNREPORTABLE`` that *becomes* reportable (because
  somebody fixed the extractor) fails here and forces the table to be corrected.

The covered set is read from `_dev/contrib/corpus/manifest.json`, whose ``expect``
fields `build_corpus.py verify` and `build --check` keep honest against the
fixtures themselves — re-identifying 89 files here would only duplicate that.
The test skips cleanly when the corpus is not present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openremap.core.manufacturers import get_extractors

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "_dev" / "contrib" / "corpus" / "manifest.json"

BOSCH = "openremap.core.manufacturers.bosch"
SIEMENS = "openremap.core.manufacturers.siemens"


def _extractor(module: str) -> str:
    return f"{module}.extractor"


# Declared family keys no code path can return: key -> (declaring module, why).
# Every entry is a *declaration*, not a fixture gap: no real dump produces these
# either (measured over the 1,685-file snapshot), so no fixture could exist and
# no test could validate the branch that would emit them.
KNOWN_UNREPORTABLE: dict[str, tuple[str, str]] = {
    "EDC2": (
        _extractor(f"{BOSCH}.edc1"),
        "extract() hardcodes ecu_family='EDC1'; EDC2 shares the layout but is never named",
    ),
    "EDC15C2": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "EDC15C3": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "EDC15C5": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "EDC15C7": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "EDC15M": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "EDC15VM+": (_extractor(f"{BOSCH}.edc15"), "extract() hardcodes ecu_family='EDC15'"),
    "M2.81": (
        _extractor(f"{BOSCH}.m2x"),
        "the DAMOS fallback keeps digits[0] of /M2\\.(\\d+)/, so M2.81 is reported as M2.8",
    ),
    "M3.8.1": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M3.8"),
    "M3.8.3": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M3.8"),
    "M3.81": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M3.8"),
    "M3.82": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M3.8"),
    "M3.83": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M3.8"),
    "M5.92": (_extractor(f"{BOSCH}.m5x"), "FAMILY_NORMALISATION maps it onto M5.9"),
    "ME7910": (
        _extractor(f"{BOSCH}.me79"),
        "extract() hardcodes ecu_family='ME7.9.10'; ME7910 is only the detection marker",
    ),
}


def declared_by() -> dict[str, set[str]]:
    """Declared family key -> modules declaring it."""
    out: dict[str, set[str]] = {}
    for extractor in get_extractors():
        for family in extractor.supported_families or []:
            out.setdefault(family, set()).add(type(extractor).__module__)
    return out


def covered_by_corpus() -> set[str]:
    """Family keys the wizard corpus reports, from the verified manifest."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    covered: set[str] = set()
    for entry in manifest["files"]:
        for key in ("ecu_family", "ecu_variant"):
            value = entry["expect"].get(key)
            if value:
                covered.add(value)
    return covered


needs_corpus = pytest.mark.skipif(
    not MANIFEST.is_file(),
    reason="wizard fixture corpus not present (_dev/contrib/corpus/manifest.json)",
)


@needs_corpus
def test_every_declared_family_is_reportable_or_documented():
    declared = declared_by()
    covered = covered_by_corpus()

    undocumented = sorted(set(declared) - covered - set(KNOWN_UNREPORTABLE))
    assert not undocumented, (
        "these family keys are declared in supported_families but no fixture "
        "reports them and they are not in KNOWN_UNREPORTABLE: "
        f"{undocumented}. Either add a fixture (`_dev/contrib/derive_families.py`) "
        "or document why the extractor can never return the key."
    )


@needs_corpus
def test_documented_unreportable_keys_are_still_unreportable():
    covered = covered_by_corpus()
    now_reportable = sorted(set(KNOWN_UNREPORTABLE) & covered)
    assert not now_reportable, (
        "these keys are listed as unreachable but a corpus fixture now reports "
        f"them: {now_reportable} — remove them from KNOWN_UNREPORTABLE"
    )


def test_documented_unreportable_keys_are_declared_where_the_table_says():
    declared = declared_by()
    for family, (module, reason) in KNOWN_UNREPORTABLE.items():
        assert reason, f"{family}: every entry needs a reason"
        assert family in declared, (
            f"{family} is listed in KNOWN_UNREPORTABLE but no extractor declares it"
        )
        assert module in declared[family], (
            f"{family} is declared by {sorted(declared[family])}, "
            f"not by {module} as the table says"
        )


def test_the_unreachable_table_is_not_the_majority():
    """Sanity bound: if this ever trips, the declarations are being abused."""
    declared = declared_by()
    assert len(KNOWN_UNREPORTABLE) < len(declared) / 2
