"""
Sorted span membership — the shared fix for the xref hit-testing hot paths.

The C166/TriCore load-base detection hit-tests each translated reference
against a list of ``(start, end)`` data spans.  The naive form

    any(s <= f < e for s, e in spans)

is O(spans) per reference, so the nested ``candidates x refs x spans`` loops
in ``refs._detect_base``, ``c166.detect_window`` and ``c166.detect_dpp_base``
dominated the arch-profile (see notes/migration/2026-08-28-rust-migration-audit.md).
Sorting the spans once and bisecting makes each test O(log spans).

Spans MAY overlap (``maps.xrefs._table_spans`` emits strided per-row ranges
alongside contiguous tables), so a naive "rightmost span whose start <= f,
then check f < its end" is NOT correct — an earlier, longer span can extend
past a later, shorter one.  The index keeps a prefix max-end array so the
containment test stays exact under overlap.

``SpanIndex.__contains__`` is a drop-in, result-identical replacement for
``any(s <= f < e for s, e in spans)`` (half-open ``[start, end)``).
"""

from __future__ import annotations

from bisect import bisect_right


class SpanIndex:
    """Immutable membership index over half-open ``[start, end)`` spans.

    Build once, then test ``f in index`` many times.  Empty and reversed
    (``start >= end``) spans never match and are dropped — exactly what the
    ``any(s <= f < e)`` form would produce for them.
    """

    __slots__ = ("_starts", "_max_ends")

    def __init__(self, spans) -> None:
        pairs = sorted((s, e) for s, e in spans if e > s)
        self._starts = [s for s, _ in pairs]
        max_ends: list[int] = []
        running = 0
        for _, e in pairs:
            if e > running:
                running = e
            max_ends.append(running)
        self._max_ends = max_ends

    def __contains__(self, f: object) -> bool:
        """True when any span contains ``f`` (``start <= f < end``)."""
        starts = self._starts
        if not starts:
            return False
        i = bisect_right(starts, f) - 1  # rightmost span with start <= f
        return i >= 0 and self._max_ends[i] > f
