"""
ECU Binary Entropy & Uniqueness Analysis
=========================================

Zero-dependency module for byte-level entropy analysis on ECU binaries.
Used by the recipe builder to verify that context anchors are statistically
unique before they are written into a recipe.

Primary entry point: ``find_unique_context()`` — called at cook time to
find a context window that produces a unique anchor in the original binary.

Backends
--------
When the native Rust extension (``openremap._rust``) is available — which
is the default for every platform with a pre-built wheel — all four public
functions dispatch directly to compiled Rust.  The pure-Python implementation
below serves as both the specification and the fallback for platforms without
a native wheel (PyPy, musl Alpine, source installs without rustup).

Algorithm
---------
1. Start with ``min_size`` bytes before the change offset.
2. Compute Shannon entropy of the candidate context.
3. Search the entire binary for ``context + ob``.
4. If entropy < threshold OR match_count > 1, double context size.
5. Repeat until both conditions are satisfied or ``max_size`` is reached.
6. If ``max_size`` is reached and still non-unique: return best effort
   with match_count > 1 (caller decides whether to reject or warn).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Tuple

# ============================================================================
# Pure-Python reference implementation — always available.
#
# These are the *specification*.  The Rust port must produce identical
# output for identical input.  Exposed as _py_* so the oracle test harness
# can compare Rust vs Python regardless of which backend is active.
# ============================================================================


def _py_shannon_entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte, rounded to 4 decimal places."""
    if not data:
        return 0.0

    total = len(data)
    counts = Counter(data)

    entropy = 0.0
    log2 = math.log(2)
    for count in counts.values():
        if count == 0:
            continue
        p = count / total
        entropy -= p * math.log(p) / log2

    return round(entropy, 4)


def _py_is_low_entropy(data: bytes, threshold: float = 2.5) -> bool:
    """True when ``shannon_entropy(data)`` is below ``threshold``."""
    return _py_shannon_entropy(data) < threshold


def _py_count_unique_in_window(
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


def _py_find_unique_context(
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

    size = min_size
    while size <= max_size:
        ctx_start = max(0, change_offset - size)
        ctx = data[ctx_start:change_offset]

        actual_size = len(ctx)
        if actual_size == 0:
            return b"", 0, 0.0, 0

        entropy = shannon_entropy(ctx)
        anchor = ctx + ob
        match_count = _py_count_unique_in_window(data, anchor, 0, len(data))

        if entropy >= entropy_threshold and match_count == 1:
            return ctx, actual_size, entropy, match_count

        size *= 2

    # max_size reached — return best effort
    ctx_start = max(0, change_offset - max_size)
    ctx = data[ctx_start:change_offset]
    entropy = shannon_entropy(ctx)
    anchor = ctx + ob
    match_count = _py_count_unique_in_window(data, anchor, 0, len(data))
    return ctx, len(ctx), entropy, match_count


# ============================================================================
# Public API — dispatch to Rust when available, fall back to pure Python.
#
# shannon_entropy / is_low_entropy: Rust (17.5× faster)
# count_unique_in_window          : Python (CPython's bytes.find is faster)
# find_unique_context             : Python (delegates to Rust entropy +
#                                   Python search — best of both)
# ============================================================================

import os as _os

_FORCE_PYTHON = _os.environ.get("OPENREMAP_FORCE_PYTHON", "").strip() in (
    "1", "true", "yes",
)

if not _FORCE_PYTHON:
    try:
        from openremap._rust import (       # type: ignore[import-untyped]
            is_low_entropy,
            shannon_entropy,
        )
        _ENTROPY_BACKEND = "rust"
    except ImportError:
        _ENTROPY_BACKEND = "python"
else:
    _ENTROPY_BACKEND = "python"


def entropy_backend() -> str:
    """Return which backend is active: ``"rust"`` or ``"python"``."""
    return _ENTROPY_BACKEND


if _ENTROPY_BACKEND == "python":
    shannon_entropy = _py_shannon_entropy            # type: ignore[assignment]
    is_low_entropy = _py_is_low_entropy              # type: ignore[assignment]

# count_unique_in_window and find_unique_context always use Python — the
# search loop (count_unique_in_window) benefits from CPython's C-level
# bytes.find (Two-Way/FASTSEARCH), and find_unique_context benefits from
# calling the Rust-backed shannon_entropy internally.
count_unique_in_window = _py_count_unique_in_window  # type: ignore[assignment]
find_unique_context = _py_find_unique_context        # type: ignore[assignment]
