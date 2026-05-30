"""
ECU Binary Entropy & Uniqueness Analysis
=========================================

Zero-dependency module for byte-level entropy analysis on ECU binaries.
Used by the recipe builder to verify that context anchors are statistically
unique before they are written into a recipe.

Primary entry point: ``find_unique_context()`` — called at cook time to
find a context window that produces a unique anchor in the original binary.

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def shannon_entropy(data: bytes) -> float:
    """
    Shannon entropy in bits per byte.

    Returns 0.0 for perfectly uniform data (e.g. all zeros),
    8.0 for perfectly random data (all 256 byte values equally likely).

    Args:
        data: Raw bytes to score.

    Returns:
        Entropy value in [0.0, 8.0], rounded to 4 decimal places.

    Examples:
        >>> shannon_entropy(b"\\x00" * 100)
        0.0
        >>> shannon_entropy(b"hello world")
        > 2.8
    """
    if not data:
        return 0.0

    total = len(data)
    # Counter is faster than manual dict accumulation for byte sequences
    counts = Counter(data)

    entropy = 0.0
    log2 = math.log(2)
    for count in counts.values():
        if count == 0:
            continue
        p = count / total
        entropy -= p * math.log(p) / log2

    return round(entropy, 4)


def is_low_entropy(data: bytes, threshold: float = 2.5) -> bool:
    """
    True when the Shannon entropy of ``data`` is below ``threshold``.

    The default threshold of 2.5 bits/byte is a well-established floor
    for distinguishing structured data from padding or repetitive fill
    patterns (0x00, 0xFF, etc.).

    Args:
        data: Raw bytes to test.
        threshold: Minimum acceptable entropy in bits/byte.

    Returns:
        True if the data has dangerously low entropy.
    """
    return shannon_entropy(data) < threshold


def count_unique_in_window(
    haystack: bytes,
    needle: bytes,
    window_start: int,
    window_end: int,
) -> int:
    """
    Count all occurrences of ``needle`` within a slice of ``haystack``.

    Uses ``bytes.find()`` in a loop — effectively Boyer-Moore-Horspool
    which is O(n) in the typical case.

    Args:
        haystack: Full binary data to search within.
        needle: Pattern to search for.
        window_start: Start offset (inclusive) of the search region.
        window_end: End offset (exclusive) of the search region.

    Returns:
        Number of occurrences found.  0 if needle is empty.

    Examples:
        >>> count_unique_in_window(b"ABABAB", b"AB", 0, 6)
        3
    """
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
    """
    Find a context anchor before ``change_offset`` that produces a unique
    ``ctx + ob`` pattern in the original binary.

    The algorithm doubles the context size geometrically until the anchor
    is both high-entropy AND unique, or until ``max_size`` is reached.

    Args:
        data:              Full original binary bytes.
        change_offset:     Offset where changed bytes begin.
        change_size:       Number of bytes in the changed region.
        ob:                Original bytes at the change location (used to
                           build the anchor pattern).
        min_size:          Starting context size in bytes (default 32).
        max_size:          Ceiling for context expansion (default 512).
        entropy_threshold: Minimum acceptable Shannon entropy in bits/byte
                           (default 2.5).

    Returns:
        ``(context_bytes, context_size, entropy, match_count)``

        ``match_count`` is the number of times ``ctx + ob`` appears in the
        ENTIRE original binary.  1 = unique.  >1 = ambiguous.

        When ``max_size`` is reached and the anchor is still non-unique or
        low-entropy, the best-effort result is returned — the caller should
        check ``match_count`` and decide whether to reject the recipe.

    Examples:
        >>> data = bytes(range(256)) * 8  # 2048 bytes of repeating 0..255
        >>> ob = bytes([0xAA, 0xBB])
        >>> # With a unique ob but low-entropy context near padding...
        >>> ctx, size, entropy, matches = find_unique_context(
        ...     data, 128, 2, ob, min_size=8, max_size=64
        ... )
        >>> size >= 8
        True
        >>> matches >= 1
        True
    """
    # --- Guard: offset must be valid ---
    if change_offset < 0 or change_offset > len(data):
        raise ValueError(
            f"change_offset {change_offset} is out of bounds "
            f"(file size: {len(data):,} bytes)."
        )

    anchor = ob  # will be extended with context — used for uniqueness search

    size = min_size
    while size <= max_size:
        ctx_start = max(0, change_offset - size)
        ctx = data[ctx_start:change_offset]

        # If we've hit the start of the file and ctx is shorter than
        # requested, that's fine — use what we have.
        actual_size = len(ctx)
        if actual_size == 0:
            # No context available at all (change at offset 0).
            # Return empty context — caller must decide.
            return b"", 0, 0.0, 0

        # --- Entropy check ---
        entropy = shannon_entropy(ctx)

        # --- Uniqueness check ---
        anchor = ctx + ob
        match_count = count_unique_in_window(data, anchor, 0, len(data))

        # Both conditions must be satisfied
        if entropy >= entropy_threshold and match_count == 1:
            return ctx, actual_size, entropy, match_count

        # --- Expand ---
        size *= 2

    # --- max_size reached without satisfying conditions ---
    # Return best effort — caller inspects match_count
    ctx_start = max(0, change_offset - max_size)
    ctx = data[ctx_start:change_offset]
    entropy = shannon_entropy(ctx)
    anchor = ctx + ob
    match_count = count_unique_in_window(data, anchor, 0, len(data))
    return ctx, len(ctx), entropy, match_count
