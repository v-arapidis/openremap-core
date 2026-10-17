"""
Golden corpus over synthetic dumps.

What this is: every case in ``manifest.json`` is a *parameter set*.  The test
regenerates the input from those parameters (deterministic seed), runs the
engine over it, and compares the result against the committed
``cases/<id>/golden.json``.  Because the input is a seed rather than a binary,
the corpus is committable and runs in CI — no ECU data, no personal data, no
size problem.

What it pins: identity (``Unknown``, deliberately), detected map candidates and
their scores, the cooked ``.remap`` recipe, the re-patched output hash, and the
two refusal layers (tampered ``ctx`` → patcher refuses with a message; tampered
``ob`` → strict validation refuses silently).  Either refusal must write
nothing — that all-or-nothing promise is the product promise.

What it cannot do: prove the extractors are *right* about real ECUs (the files
are anonymous by construction) or replace the local corpus sweeps over real
dumps.  See ``agents/behavior/synth.md``.

Updating a golden is a reviewed act, not a repair: run
``_dev/corpus/update_golden.py``, READ the diff, and commit it together with
the change that caused it.
"""

from __future__ import annotations

import pytest

from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.synth import generate_dump
from openremap.core.synth.generator import GENERATOR_VERSION
from tests.corpus.golden import (
    build_case,
    case_ids,
    expected_path,
    first_difference,
    get_case,
    load_expected,
    load_manifest,
)

CASES = case_ids()


# ---------------------------------------------------------------------------
# Manifest hygiene
# ---------------------------------------------------------------------------


class TestManifest:
    def test_every_case_has_a_committed_golden(self):
        for case_id in CASES:
            assert expected_path(case_id).is_file(), (
                f"{case_id}: run _dev/corpus/update_golden.py"
            )

    def test_case_ids_are_unique_and_descriptive(self):
        ids = [c["id"] for c in load_manifest()["cases"]]
        assert len(ids) == len(set(ids))
        assert all(i.replace("-", "").isalnum() for i in ids)

    def test_generator_version_matches_the_code(self):
        """A generator change must be declared, so a diff is attributable.

        ``stock_sha256`` in each golden already catches an undeclared change;
        this makes the omission explicit rather than mysterious.
        """
        assert load_manifest()["generator_version"] == GENERATOR_VERSION

    def test_cases_cover_both_cell_widths_and_byte_orders(self):
        params = [c["params"] for c in load_manifest()["cases"]]
        assert {p["cell_width"] for p in params} == {1, 2}
        assert {p["byte_order"] for p in params} == {"little", "big"}

    def test_no_golden_records_wall_clock_values(self):
        for case_id in CASES:
            text = expected_path(case_id).read_text(encoding="utf-8")
            assert "<normalised:wall-clock>" in text, (
                f"{case_id}: recipe timestamps must be normalised"
            )


# ---------------------------------------------------------------------------
# The golden itself
# ---------------------------------------------------------------------------


class TestGolden:
    @pytest.mark.parametrize("case_id", CASES)
    def test_matches_the_committed_golden(self, case_id):
        expected = load_expected(case_id)
        actual = build_case(get_case(case_id))
        difference = first_difference(expected, actual)
        assert difference is None, (
            f"behaviour drifted in case '{case_id}' at {difference}\n"
            "If the change was intentional: run _dev/corpus/update_golden.py, "
            "read the diff, and commit it with the change that caused it."
        )

    @pytest.mark.parametrize("case_id", CASES)
    def test_input_regenerates_byte_identically(self, case_id):
        """The pinned input hash is what makes the corpus reproducible."""
        case = get_case(case_id)
        golden = load_expected(case_id)
        dump = generate_dump(**case["params"])
        assert dump.sha256 == golden["stock_sha256"]
        assert dump.tuned(percent=case["tune_percent"]).sha256 == golden["tuned_sha256"]


# ---------------------------------------------------------------------------
# Ground truth — the assertion that a snapshot alone cannot make
# ---------------------------------------------------------------------------


class TestGroundTruth:
    @pytest.mark.parametrize("case_id", CASES)
    def test_every_planted_map_is_detected_exactly(self, case_id):
        case = get_case(case_id)
        dump = generate_dump(**case["params"])
        tables = scan_map_tables(dump.data)

        for planted in dump.maps:
            hit = next((t for t in tables if t.offset == planted.offset), None)
            assert hit is not None, (
                f"{case_id}: planted map {planted.map_id} "
                f"({planted.cols}x{planted.rows}) at {planted.offset:#x} not detected"
            )
            assert (hit.cols, hit.rows, hit.cell_width, hit.byte_order) == (
                planted.cols,
                planted.rows,
                planted.cell_width,
                planted.byte_order,
            ), f"{case_id}: map {planted.map_id} detected with the wrong shape"

    @pytest.mark.parametrize("case_id", CASES)
    def test_tuning_touches_only_the_planted_cells(self, case_id):
        """`cook` must see the tune we planted — and nothing else."""
        case = get_case(case_id)
        stock = generate_dump(**case["params"])
        tuned = stock.tuned(percent=case["tune_percent"])

        changed = [
            i for i, (a, b) in enumerate(zip(stock.data, tuned.data)) if a != b
        ]
        assert changed, f"{case_id}: the tune changed nothing"
        for offset in changed:
            assert any(m.offset <= offset < m.end for m in stock.maps), (
                f"{case_id}: byte {offset:#x} changed outside every planted map"
            )

    @pytest.mark.parametrize("case_id", CASES)
    def test_identity_is_unknown_by_construction(self, case_id):
        golden = load_expected(case_id)
        assert golden["identify"]["manufacturer"] is None
        assert golden["identify"]["ecu_family"] is None
        assert golden["identify"]["match_key"] is None
