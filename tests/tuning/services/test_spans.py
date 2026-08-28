"""
SpanIndex tests — the overlap-safe bisect replacement for the linear
``any(s <= f < e for s, e in spans)`` hot-path hit-testing.

The parity property test pins the core guarantee: ``f in SpanIndex(spans)``
is exactly ``any(s <= f < e for s, e in spans)`` for every offset, so the
refactored base-detection loops produce identical results.
"""

from __future__ import annotations

import random

from openremap.core.arch.spans import SpanIndex


def test_basic_membership_half_open():
    idx = SpanIndex([(10, 20)])
    assert 10 in idx      # start is inclusive
    assert 15 in idx
    assert 19 in idx
    assert 20 not in idx  # end is exclusive
    assert 9 not in idx
    assert 21 not in idx


def test_multiple_disjoint_spans():
    idx = SpanIndex([(0, 5), (10, 15), (20, 25)])
    for f in (0, 4, 10, 14, 20, 24):
        assert f in idx
    for f in (5, 9, 15, 19, 25, 26):
        assert f not in idx


def test_overlapping_and_nested_spans():
    # (0, 100) wraps (50, 60); a query past the shorter span's end must
    # still resolve through the longer one via the prefix max-end.
    idx = SpanIndex([(0, 100), (50, 60)])
    assert 40 in idx   # only (0, 100)
    assert 55 in idx   # both
    assert 80 in idx   # only (0, 100) — the earlier, longer span wins
    assert 100 not in idx


def test_nested_short_span_does_not_shadow_longer():
    # The classic case the naive "rightmost start" fails: a short span sits
    # inside a long one, and f is inside the long span but AFTER the short.
    idx = SpanIndex([(0, 1000), (500, 600)])
    assert 900 in idx
    assert 1000 not in idx


def test_empty_spans():
    assert 5 not in SpanIndex([])


def test_empty_and_reversed_spans_dropped():
    # (5, 5) and (20, 10) never match the s <= f < e form.
    idx = SpanIndex([(5, 5), (20, 10), (100, 110)])
    assert 50 not in idx
    assert 105 in idx


def test_unsorted_input_is_sorted_internally():
    idx = SpanIndex([(20, 30), (0, 10), (10, 20)])
    assert 5 in idx
    assert 15 in idx
    assert 25 in idx
    assert 30 not in idx


def test_negative_offset_never_matches():
    idx = SpanIndex([(0, 10)])
    assert -1 not in idx
    assert -100 not in idx


def test_parity_with_linear_scan():
    """Property: SpanIndex.__contains__ == any(s <= f < e) on the same spans."""
    rng = random.Random(20260828)
    spans = []
    for _ in range(40):
        s = rng.randrange(0, 1000)
        e = s + rng.randrange(1, 200)  # always s < e (valid span)
        spans.append((s, e))
    # A few overlapping/nested ones on purpose.
    spans += [(100, 900), (300, 320), (0, 5)]
    idx = SpanIndex(spans)
    for f in range(-10, 1010):
        expected = any(s <= f < e for s, e in spans)
        assert (f in idx) == expected, f"offset {f} disagreed"
