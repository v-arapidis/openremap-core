"""
The contribution wizard's engine: measurement, rendering, verification.

These tests are written around the two claims the wizard makes to a contributor,
because a wizard that is confidently wrong wastes the contributor's afternoon:

1. *"this marker is stable in your family and occurs in no family we already
   recognise"* — tested against crafted dumps with a known answer, including the
   cases where the honest answer is "no such marker exists";
2. *"the extractor I generated claims your family and steals nobody else's"* —
   tested by executing the generated source, and by **breaking it on purpose**
   (dropping the exclusion guard) to prove the check can fail.

The guard scenario is built from a synthetic fixture pool rather than the shipped
corpus: with the real fixtures the pool file happened to have a different size,
so the generated size gate rejected it on its own and the guard test would have
passed without proving anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openremap.core.services.wizard import (
    build_fixture,
    check_source,
    fixture_count,
    fixtures_available,
    known_fixtures,
    prepare,
    reset_cache,
)
from openremap.core.services.wizard.check import check_evidence_clearing
from openremap.core.services.wizard.collisions import marker_collisions
from openremap.core.services.wizard.emit import Spec, render_extractor
from openremap.core.services.wizard.fixtures import KnownFixture, find_recipes_dir
from openremap.core.services.wizard.report import analyze
from openremap.core.services.wizard.verify import verify_generated

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "_dev" / "contrib" / "corpus" / "manifest.json"
RECIPES_SUBPATH = Path("_dev") / "contrib" / "recipes"
SIZE = 0x40000
MARKER = b"45/1/SID206/9/5033/DAMOS161/8D0907551"


def make_dump(
    *, marker: bytes = MARKER, sw: str = "1037359650", size: int = SIZE, at: int = 0x100
) -> bytes:
    """A stub dump: erased flash + a marker + a Bosch-style HW/SW ident pair."""
    buf = bytearray(b"\xff" * size)
    buf[at : at + len(marker)] = marker
    ident = f"0261204270 {sw}".encode()
    buf[0x400 : 0x400 + len(ident)] = ident
    return bytes(buf)


def make_bare_dump(*, marker: bytes, size: int = SIZE, at: int = 0x100) -> bytes:
    """A dump whose only printable run is *marker*.

    Used where the test is about that one run: a dump that also carries an ident
    pair gives the report a second, cleaner candidate to recommend, and the
    assertion then measures the wrong thing.
    """
    buf = bytearray(b"\xff" * size)
    buf[at : at + len(marker)] = marker
    return bytes(buf)


def synthetic_fixture(
    *, marker: bytes, family: str = "FOO9", size: int = SIZE
) -> KnownFixture:
    """A known-family fixture that shares a marker with the contributor's family.

    Size is deliberately the same as the contributor's dump: otherwise the
    generated size gate rejects it and any "it steals nothing" result is
    vacuous.
    """
    spec = {
        "name": f"acme-{family.lower()}-{size // 1024}k.mimic",
        "manufacturer": "Acme",
        "family": family,
        "variant": None,
        "file_size": size,
        "fill": "FF",
        "plants": [
            {"at": "0x200", "seq": [{"hex": marker.hex()}]},
            {"at": "0x300", "seq": [{"hex": family.encode().hex()}]},
        ],
    }
    return KnownFixture(
        name=spec["name"],
        manufacturer=spec["manufacturer"],
        family=family,
        variant=None,
        data=build_fixture(spec),
        spec=spec,
    )


# ---------------------------------------------------------------------------
# Fixture loading — the wizard reads the checkout, there is no second copy
# ---------------------------------------------------------------------------


def test_fixtures_come_from_the_checkout():
    """One source of truth: the repository's own recipes."""
    assert fixtures_available() is True
    assert fixture_count() == len(known_fixtures()) == 89
    assert find_recipes_dir() is not None
    assert find_recipes_dir().parent.name == "contrib"


def test_loaded_fixtures_rebuild_the_committed_corpus():
    """The wizard's loader and the corpus builder must agree byte-for-byte.

    The wizard trusts these bytes to answer collision questions, and the corpus
    is what a reviewer sees in the diff; if they disagree, one of them is lying
    about what a family looks like.
    """
    if not CORPUS.is_file():
        pytest.skip("mimic corpus not present")
    expected = {e["name"]: e["sha256"] for e in json.loads(CORPUS.read_text())["files"]}
    assert {f.name for f in known_fixtures()} == set(expected)
    mismatched = [
        fixture.name
        for fixture in known_fixtures()
        if hashlib.sha256(fixture.data).hexdigest() != expected[fixture.name]
    ]
    assert not mismatched, f"fixtures differ from the corpus: {mismatched}"


def test_a_recipe_written_into_the_tree_is_seen_next_run(tmp_path, monkeypatch):
    """The bug the shipped copy caused: the wizard could not see its own output.

    A contributor adds a family, then re-runs the wizard — the new fixture must
    be part of the collision check, without any export step.
    """
    recipes = tmp_path / RECIPES_SUBPATH
    recipes.mkdir(parents=True)
    (recipes / "acme-foo9.json").write_text(
        json.dumps(
            {
                "manufacturer": "Acme",
                "file_size": 1024,
                "fill": "FF",
                "plants": [{"at": "0x10", "seq": [{"hex": b"FOO9_MARKER".hex()}]}],
                "expect": {"manufacturer": "Acme", "ecu_family": "FOO9"},
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    reset_cache()
    try:
        assert fixture_count() == 1
        assert known_fixtures()[0].name == "acme-foo9-1k.mimic"
        assert known_fixtures()[0].family == "FOO9"
    finally:
        reset_cache()


def test_fixture_family_keys_are_labelled():
    for fixture in known_fixtures():
        assert fixture.family
        assert fixture.manufacturer


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def test_marker_stable_across_dumps_is_found():
    report = analyze([("a.bin", make_dump()), ("b.bin", make_dump(sw="1037359651"))])
    assert report["recommended"] == MARKER.decode("latin-1")
    top = report["candidates"][0]
    assert top["clean"] is True
    assert top["offsets"] == {"a.bin": 0x100, "b.bin": 0x100}


def test_marker_missing_from_one_dump_is_not_a_family_marker():
    """A run that only one dump has cannot identify the family."""
    report = analyze(
        [("a.bin", make_dump()), ("b.bin", make_dump(marker=b"OTHER_MARKER_HERE"))]
    )
    assert MARKER.decode("latin-1") not in [c["marker"] for c in report["candidates"]]


def test_single_dump_says_stability_was_not_verified():
    report = analyze([("a.bin", make_dump())])
    assert any("Only one dump" in note for note in report["notes"])
    assert "stability unverified" in " ".join(report["candidates"][0]["reasons"])


def test_collision_with_a_real_family_is_reported():
    """Planting another family's ident token must be caught, with its fixtures."""
    report = analyze([("a.bin", make_bare_dump(marker=b"EDC16C34"))])
    top = report["candidates"][0]
    assert top["marker"] == "EDC16C34"
    assert top["clean"] is False
    assert "EDC16C34" in top["collision_families"]
    assert top["collisions"][0]["offset"] >= 0
    assert report["recommended"] is None


def test_shared_vocabulary_is_explained_not_hidden():
    """`MOTRONIC` is a stable run in many families — the answer is a reason."""
    report = analyze([("a.bin", make_bare_dump(marker=b"MOTRONIC"))])
    top = report["candidates"][0]
    assert top["marker"] == "MOTRONIC"
    assert top["generic"] is not None
    assert len(top["collision_families"]) > 3
    assert "shared vocabulary" in top["verdict"]
    assert report["recommended"] is None


def test_a_dump_with_nothing_printable_explains_itself():
    report = analyze([("a.bin", b"\x00" * 4096)])
    assert report["candidates"] == []
    assert report["candidate_stats"]["distinct_runs"] == 0
    assert any("No marker candidates survived" in note for note in report["notes"])


def test_already_claimed_is_reported_not_ignored():
    """If an extractor already wins on the dump, the contributor must be told."""
    fixture = next(f for f in known_fixtures() if f.family == "EDC16")
    report = analyze([("real-ish.mimic", fixture.data)])
    assert report["already_handled"] is True
    assert report["claimed_by"]["extractor"].endswith("edc16.extractor")


def test_fields_split_stable_from_per_dump():
    report = analyze([("a.bin", make_dump()), ("b.bin", make_dump(sw="1037359651"))])
    by_kind = {f["kind"]: f for f in report["fields"]}
    assert by_kind["bosch_hw"]["stable"] is True
    assert by_kind["bosch_hw"]["value"] == "0261204270"
    assert by_kind["bosch_sw"]["stable"] is False
    assert "match_key" in by_kind["bosch_sw"]["suggested_use"]


def test_a_familys_own_fixture_is_not_a_collision():
    """Once a family is in the corpus, its marker is in its own fixture — fine.

    Without this exclusion a contributor iterating on a family they already added
    is told "no collision-free marker" by their own file, and the guard harvested
    from it would reject their own dumps.
    """
    own = synthetic_fixture(marker=MARKER, family="SID206")
    other = synthetic_fixture(marker=b"OTHER_FAMILY_TOK", family="FOO9")
    reuse = KnownFixture(
        name=own.name,
        manufacturer=own.manufacturer,
        family=own.family,
        variant=own.variant,
        data=own.data,
        spec=own.spec,
    )

    report = analyze([("a.bin", make_dump())], fixtures=(reuse, other), exclude_families=("SID206",))
    assert report["excluded_own_fixtures"] == [reuse.name]
    assert any("excluded from collision checking" in note for note in report["notes"])
    top = next(c for c in report["candidates"] if c["marker"] == MARKER.decode("latin-1"))
    assert top["collisions"] == []
    assert report["recommended"] == MARKER.decode("latin-1")


def test_the_guard_harvest_ignores_the_familys_own_fixture():
    """A guard derived from your own fixture would reject your own files."""
    own = synthetic_fixture(marker=MARKER, family="SID206")
    other = synthetic_fixture(marker=b"OTHER_FAMILY_TOK", family="FOO9")
    result = prepare(
        [("a.bin", make_dump()), ("b.bin", make_dump(sw="1037359651"))],
        brand="Siemens",
        family="SID206",
        fixtures=(own, other),
    )
    assert result["spec"]["exclusions"] == []
    assert result["verification"]["ok"] is True


def test_only_our_own_fixture_means_theft_cannot_be_checked():
    """An empty pool must never read as a pass — the honest word is 'cannot'."""
    own = synthetic_fixture(marker=MARKER, family="SID206")
    result = prepare(
        [("a.bin", make_dump())], brand="Siemens", family="SID206", fixtures=(own,)
    )
    assert result["verification"]["ok"] is False
    stolen = next(
        c for c in result["verification"]["checks"] if c["name"] == "steals no other family"
    )
    assert "cannot be checked" in stolen["detail"]


def test_without_fixtures_it_measures_but_never_recommends(monkeypatch, tmp_path):
    """An installed wizard has no recipes: measure, say so, advise nothing.

    Recommending a marker that was never checked against other families is the
    one failure this tool exists to prevent, so an unchecked ranking must not
    produce a recommendation or an artifact.
    """
    monkeypatch.chdir(tmp_path)          # no _dev/contrib/recipes anywhere
    reset_cache()
    from openremap.core.services.wizard.fixtures import find_recipes_dir

    monkeypatch.setattr(
        "openremap.core.services.wizard.fixtures.find_recipes_dir", lambda start=None: None
    )
    try:
        report = analyze([("a.bin", make_dump())])
        assert report["collisions_checked"] is False
        assert report["recommended"] is None
        # measurement still stands — the runs are found, they are just unranked
        assert [c["marker"] for c in report["candidates"]]
        assert all(c["collisions"] == [] for c in report["candidates"])
        assert any("Collisions were NOT checked" in note for note in report["notes"])

        with pytest.raises(ValueError, match="no fixture recipes found"):
            prepare([("a.bin", make_dump())], brand="Siemens", family="SID206")
    finally:
        reset_cache()


def test_measure_only_mode_needs_no_checkout(monkeypatch, tmp_path):
    """The opt-in half: measure anywhere, but never present it as a result."""
    monkeypatch.setattr(
        "openremap.core.services.wizard.fixtures.find_recipes_dir",
        lambda start=None: None,
    )
    reset_cache()
    try:
        result = prepare(
            [("a.bin", make_dump())],
            brand="Siemens",
            family="SID206",
            measure_only=True,
        )
        assert result["mode"] == "measure"
        assert result["artifacts"] is None
        assert result["verification"] is None
        assert result["paths"] == {}
        assert result["analysis"]["recommended"] is None
        assert result["analysis"]["collisions_checked"] is False
        assert [c["marker"] for c in result["analysis"]["candidates"]]
    finally:
        reset_cache()


def test_measure_only_needs_no_brand_or_family(monkeypatch):
    """Measurement must run before the user knows the family name.

    The recipes are hidden as well as the cwd moved: with a checkout present the
    call takes the contribute path, which legitimately needs both names.
    """
    monkeypatch.setattr(
        "openremap.core.services.wizard.fixtures.find_recipes_dir",
        lambda start=None: None,
    )
    reset_cache()
    try:
        result = prepare([("a.bin", make_dump())], measure_only=True)
        assert result["mode"] == "measure"
        assert result["analysis"]["recommended"] is None
    finally:
        reset_cache()


def test_artifacts_need_brand_and_family():
    with pytest.raises(ValueError, match="brand and family are required"):
        prepare([("a.bin", make_dump())], measure_only=False)


def test_the_same_dump_twice_is_measured_once_and_says_so():
    """Two copies of one calibration cannot prove a marker is stable."""
    report = analyze([("a.bin", make_dump()), ("a.bin", make_dump())])
    assert any("identical" in note for note in report["notes"])
    assert [c["marker"] for c in report["candidates"]]
    assert "present in all 2 dumps" not in " ".join(report["candidates"][0]["reasons"])


def test_an_empty_fixture_pool_fails_the_review_bar():
    """The check that stops theft must never be green because nothing was loaded."""
    from openremap.core.services.wizard.verify import check_extractor

    result = check_extractor(_dummy_extractor(), fixtures=())
    stolen = next(c for c in result["checks"] if c["name"] == "steals no other family")
    assert stolen["ok"] is False
    assert "cannot be checked" in stolen["detail"]


def _dummy_extractor():
    from openremap.core.manufacturers.base import BaseManufacturerExtractor

    class Dummy(BaseManufacturerExtractor):
        @property
        def name(self):
            return "Acme"

        @property
        def supported_families(self):
            return ["FOO9"]

        def can_handle(self, data):
            self._set_evidence(["SIZE_MATCH"])
            return True

        def extract(self, data, filename="unknown.bin"):
            return {
                "manufacturer": self.name,
                "ecu_family": "FOO9",
                "ecu_variant": None,
                "file_size": len(data),
                "md5": "",
                "sha256_first_64kb": "",
                "software_version": None,
                "hardware_number": None,
                "calibration_id": None,
                "oem_part_number": None,
                "match_key": None,
            }

    return Dummy()


# ---------------------------------------------------------------------------
# Rendering + verification
# ---------------------------------------------------------------------------


def test_prepare_renders_a_verified_skeleton():
    result = prepare(
        [("a.bin", make_dump()), ("b.bin", make_dump(sw="1037359651"))],
        brand="Siemens",
        family="SID206",
    )
    assert result["verification"]["ok"] is True
    checks = {c["name"]: c["ok"] for c in result["verification"]["checks"]}
    assert checks["claims its own fixture"] is True
    assert checks["steals no other family"] is True
    assert checks["clears evidence on a miss"] is True

    artifacts = result["artifacts"]
    assert repr(MARKER) in artifacts["extractor"]
    assert "class SiemensSID206Extractor" in artifacts["extractor"]
    assert result["paths"]["recipe"] == "_dev/contrib/recipes/siemens-sid206.json"
    assert MARKER.decode("latin-1") in artifacts["report"]


def test_generated_fixture_recipe_rebuilds_the_accepted_file():
    """The recipe written to disk must produce the bytes the skeleton accepted."""
    result = prepare([("a.bin", make_dump())], brand="Siemens", family="SID206")
    recipe = result["artifacts"]["recipe"]
    assert build_fixture(
        {
            "name": recipe["ecu_family"],
            "file_size": recipe["file_size"],
            "fill": recipe["fill"],
            "plants": recipe["plants"],
        }
    ) == build_fixture(
        {
            "name": "own",
            "file_size": SIZE,
            "fill": "FF",
            "plants": [{"at": "0x100", "seq": [{"hex": MARKER.hex()}]}],
        }
    )


def test_no_clean_marker_is_a_clear_refusal():
    """When every candidate collides, the wizard refuses instead of guessing."""
    with pytest.raises(ValueError, match="no collision-free marker"):
        prepare(
            [("a.bin", make_bare_dump(marker=b"EDC16C34"))],
            brand="Siemens",
            family="X9",
        )


def test_a_chosen_marker_that_is_not_in_the_dump_is_refused():
    with pytest.raises(ValueError):
        prepare(
            [("a.bin", make_dump())],
            brand="Siemens",
            family="X9",
            marker=b"MARKER_NOT_IN_THIS_DUMP",
        )


def test_identifier_validation_rejects_path_traversal():
    """Brand/family names become class names and file paths."""
    with pytest.raises(ValueError, match="invalid family"):
        Spec(brand="Siemens", family="../../etc/passwd", marker=MARKER).validate()


def test_generated_extractor_rejects_a_wrong_size_and_clears_evidence():
    result = prepare([("a.bin", make_dump())], brand="Siemens", family="SID206")
    from openremap.core.services.wizard.verify import load_extractor

    extractor = load_extractor(result["artifacts"]["extractor"])
    assert extractor.can_handle(make_dump()) is True
    assert extractor.last_detection_evidence
    assert extractor.can_handle(b"\xff" * 0x1000) is False
    assert extractor.last_detection_evidence == ()


# ---------------------------------------------------------------------------
# Exclusion guards — and proof that the check can fail
# ---------------------------------------------------------------------------


def _shared_marker_pool(marker: bytes) -> tuple[KnownFixture, ...]:
    return (synthetic_fixture(marker=marker),)


def test_collision_with_a_guardable_fixture_gets_a_guard():
    shared = b"SHARED_MARKER_42"
    pool = _shared_marker_pool(shared)
    dumps = [("a.bin", make_dump(marker=shared, at=0x100))]

    result = prepare(
        dumps, brand="Siemens", family="SID206", marker=shared, fixtures=pool
    )

    assert result["spec"]["collides_with"] == ["FOO9"]
    assert result["spec"]["exclusions"] == [{"marker": "FOO9", "why": "Acme FOO9"}]
    assert result["verification"]["ok"] is True
    assert any(
        c["name"] == "steals no other family" and c["ok"]
        for c in result["verification"]["checks"]
    )


def test_without_the_guard_the_same_skeleton_is_caught():
    """The other half of the guard test: the check must be able to fail.

    Same spec, same bytes — only the exclusion marker is removed. If the
    verification still passed, "steals no other family" would be decoration.
    """
    shared = b"SHARED_MARKER_42"
    pool = _shared_marker_pool(shared)
    own = build_fixture(
        {
            "name": "own",
            "file_size": SIZE,
            "fill": "FF",
            "plants": [{"at": "0x100", "seq": [{"hex": shared.hex()}]}],
        }
    )
    spec = Spec(
        brand="Siemens",
        family="SID206",
        marker=shared,
        sizes=[SIZE],
        exclusions=[],  # ← the guard is gone
        marker_offset=0x100,
    )
    result = verify_generated(
        render_extractor(spec), family="SID206", own_fixture=own, fixtures=pool
    )
    assert result["ok"] is False
    assert len(result["stolen"]) == 1 and pool[0].name in result["stolen"][0]
    assert "exclusion guard" in dict(
        (c["name"], c["detail"]) for c in result["checks"]
    )["steals no other family"]


def test_a_collision_with_no_derivable_guard_fails_instead_of_pretending():
    """A fixture whose recipe never spells its own name cannot be guarded."""
    naked = KnownFixture(
        name="acme-ghost.mimic",
        manufacturer="Acme",
        family="GHOST",
        variant=None,
        data=build_fixture(
            {
                "name": "ghost",
                "file_size": SIZE,
                "fill": "FF",
                "plants": [{"at": "0x200", "seq": [{"hex": b"SHARED_MARKER_42".hex()}]}],
            }
        ),
        spec={
            "family": "GHOST",
            "variant": None,
            "plants": [{"at": "0x200", "seq": [{"hex": b"SHARED_MARKER_42".hex()}]}],
        },
    )
    result = prepare(
        [("a.bin", make_dump(marker=b"SHARED_MARKER_42"))],
        brand="Siemens",
        family="SID206",
        marker=b"SHARED_MARKER_42",
        fixtures=(naked,),
    )
    assert result["spec"]["exclusions"] == []
    assert result["verification"]["ok"] is False
    assert len(result["verification"]["stolen"]) == 1
    assert naked.name in result["verification"]["stolen"][0]


# ---------------------------------------------------------------------------
# `contribute check` — the static half
# ---------------------------------------------------------------------------


def test_evidence_clearing_is_detected_statically():
    clean = '''
from openremap.core.manufacturers.base import BaseManufacturerExtractor

class X(BaseManufacturerExtractor):
    def can_handle(self, data):
        if not data:
            self._set_evidence()
            return False
        self._set_evidence(["SIZE_MATCH"])
        return True
'''
    assert check_evidence_clearing(clean).ok is True

    broken = clean.replace("            self._set_evidence()\n            return False",
                           "            return False")
    finding = check_evidence_clearing(broken)
    assert finding.ok is False
    assert "return False" in finding.detail


def test_check_reports_an_unregistered_extractor():
    source = Path(
        "openremap/core/manufacturers/siemens/sid803/extractor.py"
    ).read_text(encoding="utf-8")
    result = check_source(source, module_name="acme.ghost.extractor")
    failed = {c["name"] for c in result["checks"] if not c["ok"]}
    assert "reachable from the registry" in failed


def test_check_passes_on_a_shipped_extractor():
    """The bar has to be satisfiable: every shipped extractor already meets it."""
    source = Path(
        "openremap/core/manufacturers/siemens/sid803/extractor.py"
    ).read_text(encoding="utf-8")
    result = check_source(
        source, module_name="openremap.core.manufacturers.siemens.sid803.extractor"
    )
    assert result["ok"] is True
    assert result["family"] == "SID803"


def test_every_shipped_extractor_meets_the_review_bar():
    """If the repository ever drifts from its own bar, this says which family."""
    from openremap.core.manufacturers import get_extractors

    from openremap.core.services.wizard.verify import check_extractor

    failures = []
    for extractor in get_extractors():
        result = check_extractor(extractor)
        if not result["ok"]:
            failures.append(
                (
                    type(extractor).__module__,
                    [c["name"] for c in result["checks"] if not c["ok"]],
                )
            )
    assert not failures, f"extractors failing the review bar: {failures}"


def test_marker_collisions_are_measured_against_the_shipped_pool():
    collisions = marker_collisions(b"EDC16C34")
    assert collisions and collisions[0].family == "EDC16C34"
    assert marker_collisions(b"NO_SUCH_TOKEN_ANYWHERE_XYZ") == []
