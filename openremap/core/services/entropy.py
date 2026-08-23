"""
ECU Binary Entropy & Uniqueness Analysis
=========================================

``shannon_entropy`` and ``is_low_entropy`` run on the compiled Rust backend
(36–75× faster than pure Python).  ``count_unique_in_window`` and
``find_unique_context`` use CPython's C-level ``bytes.find`` (Two-Way /
FASTSEARCH) which is slightly faster than the Rust memchr loop for ECU
context-anchor workloads.
"""

from __future__ import annotations

from typing import Tuple

# ============================================================================
# Rust-accelerated — always available (no fallback).
# ============================================================================

from openremap._rust import (       # type: ignore[import-untyped]
    is_low_entropy,
    shannon_entropy,
)

_ENTROPY_BACKEND = "rust"


def entropy_backend() -> str:
    """Return which backend is active: ``"rust"``."""
    return _ENTROPY_BACKEND


# ============================================================================
# Python — context-anchor search (CPython bytes.find beats memchr here).
# ============================================================================


def find_all_offsets(haystack: bytes, needle: bytes) -> list[int]:
    """Return every offset where *needle* occurs (possibly overlapping) in
    *haystack*, using CPython's ``bytes.find`` (Two-Way / FASTSEARCH)."""
    if not needle:
        return []
    offsets: list[int] = []
    pos = 0
    while True:
        p = haystack.find(needle, pos)
        if p == -1:
            break
        offsets.append(p)
        pos = p + 1
    return offsets


def count_unique_in_window(
    haystack: bytes,
    needle: bytes,
    window_start: int,
    window_end: int,
) -> int:
    """Count all (possibly overlapping) occurrences of *needle* within a
    bounded region of *haystack*.  Returns 0 when *needle* is empty."""
    if not needle:
        return 0

    region = haystack[window_start:window_end]
    count = 0
    pos = 0
    while True:
        p = region.find(needle, pos)
        if p == -1:
            break
        count += 1
        pos = p + 1

    return count


def find_unique_context(
    data: bytes,
    change_offset: int,
    change_size: int,
    ob: bytes,
    min_size: int = 32,
    max_size: int = 512,
    entropy_threshold: float = 2.5,
) -> Tuple[bytes, int, float, int]:
    """Find a context anchor before *change_offset* whose ``ctx + ob``
    pattern is unique in the entire *data* AND has entropy above
    *entropy_threshold*.

    The context window doubles geometrically from *min_size* until both
    conditions are satisfied or *max_size* is reached.  Returns
    ``(context_bytes, context_size, entropy, match_count)``.
    """
    if change_offset < 0 or change_offset > len(data):
        raise ValueError(
            f"change_offset {change_offset} is out of bounds "
            f"(file size: {len(data):,} bytes)."
        )

    if min_size > max_size:
        raise ValueError(
            f"min_size ({min_size}) must be <= max_size ({max_size})."
        )

    size = min_size
    while size <= max_size:
        ctx_start = max(0, change_offset - size)
        ctx = data[ctx_start:change_offset]

        actual_size = len(ctx)
        if actual_size == 0:
            # Degenerate case: change at offset 0 — no bytes before it to use
            # as context.  The effective anchor is just ob (plus ctx_after,
            # captured by the caller).  Return its real occurrence count so
            # Guard 3 and the annotator see a truthful uniqueness signal
            # instead of an ambiguous 0.
            match_count = count_unique_in_window(data, ob, 0, len(data))
            return b"", 0, 0.0, match_count

        entropy = shannon_entropy(ctx)
        anchor = ctx + ob
        match_count = count_unique_in_window(data, anchor, 0, len(data))

        if entropy >= entropy_threshold and match_count == 1:
            return ctx, actual_size, entropy, match_count

        size *= 2

    # max_size reached — return best effort
    ctx_start = max(0, change_offset - max_size)
    ctx = data[ctx_start:change_offset]
    entropy = shannon_entropy(ctx)
    anchor = ctx + ob
    match_count = count_unique_in_window(data, anchor, 0, len(data))
    return ctx, len(ctx), entropy, match_count
