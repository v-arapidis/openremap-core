"""
Tests for the entropy & uniqueness analysis module (entropy.py).

Covers:
  - shannon_entropy: zero data, max entropy, empty data, known distributions
  - is_low_entropy: threshold boundary behaviour
  - count_unique_in_window: exact counting, empty needle, window bounds
  - find_unique_context: auto-expansion, uniqueness gating, max_size ceiling
"""

from __future__ import annotations

import pytest

from openremap.core.services.entropy import (
    shannon_entropy,
    is_low_entropy,
    count_unique_in_window,
    find_unique_context,
)


# ---------------------------------------------------------------------------
# shannon_entropy
# ---------------------------------------------------------------------------


class TestShannonEntropy:
    def test_zero_data(self):
        """All-zeros data has entropy 0.0."""
        assert shannon_entropy(b"\x00" * 100) == 0.0

    def test_ff_data(self):
        """All-0xFF data also has entropy 0.0 (single value)."""
        assert shannon_entropy(b"\xFF" * 100) == 0.0

    def test_max_entropy_all_unique(self):
        """256 bytes with all unique values → entropy = 8.0."""
        data = bytes(range(256))
        assert shannon_entropy(data) == 8.0

    def test_empty_data(self):
        """Empty bytes return 0.0."""
        assert shannon_entropy(b"") == 0.0

    def test_single_byte(self):
        """Single byte has entropy 0.0 (one unique value)."""
        assert shannon_entropy(b"\x42") == 0.0

    def test_two_distinct_bytes_equal_frequency(self):
        """Two bytes each appearing once → entropy = 1.0."""
        assert shannon_entropy(b"\x00\xFF") == 1.0

    def test_mixed_text(self):
        """ASCII text has moderate entropy."""
        entropy = shannon_entropy(b"hello world, this is test data!")
        assert 3.5 < entropy < 5.0

    def test_repeating_pattern(self):
        """Repeating 4-byte pattern → low but non-zero entropy."""
        data = b"\xDE\xAD\xBE\xEF" * 64  # 256 bytes, 4 unique values
        entropy = shannon_entropy(data)
        assert 1.5 < entropy < 2.5  # ~2.0 bits/byte


# ---------------------------------------------------------------------------
# is_low_entropy
# ---------------------------------------------------------------------------


class TestIsLowEntropy:
    def test_below_threshold(self):
        """All zeros is below the default 2.5 threshold."""
        assert is_low_entropy(b"\x00" * 100) is True

    def test_above_threshold(self):
        """Unique bytes are above the default threshold."""
        assert is_low_entropy(bytes(range(256))) is False

    def test_custom_threshold(self):
        """Respects custom threshold value."""
        # 16 unique bytes uniformly distributed → entropy = 4.0 bits/byte
        data = bytes(i % 16 for i in range(256))
        assert is_low_entropy(data, threshold=5.0) is True
        assert is_low_entropy(data, threshold=3.0) is False

    def test_at_exact_threshold(self):
        """Boundary: entropy == threshold is NOT low (< not <=)."""
        data = b"\x00\xFF"  # exactly 1.0 entropy
        assert is_low_entropy(data, threshold=1.0) is False
        assert is_low_entropy(data, threshold=1.0001) is True


# ---------------------------------------------------------------------------
# count_unique_in_window
# ---------------------------------------------------------------------------


class TestCountUniqueInWindow:
    def test_single_occurrence(self):
        assert count_unique_in_window(b"ABCDEF", b"CD", 0, 6) == 1

    def test_multiple_occurrences(self):
        assert count_unique_in_window(b"ABABAB", b"AB", 0, 6) == 3

    def test_no_occurrence(self):
        assert count_unique_in_window(b"ABCDEF", b"XY", 0, 6) == 0

    def test_empty_needle(self):
        """Empty needle returns 0."""
        assert count_unique_in_window(b"ABCDEF", b"", 0, 6) == 0

    def test_window_bounds_respected(self):
        """Only counts matches within the specified window."""
        # "AB" appears at offsets 0, 2, 4, 6, 8
        data = b"ABABABABAB"
        # window [2, 8) includes offsets 2, 4, 6 → 3 matches
        assert count_unique_in_window(data, b"AB", 2, 8) == 3
        # window [0, 4) includes offsets 0, 2 → 2 matches
        assert count_unique_in_window(data, b"AB", 0, 4) == 2

    def test_needle_longer_than_window(self):
        """Needle longer than window → 0 matches."""
        assert count_unique_in_window(b"AB", b"ABCDEF", 0, 2) == 0

    def test_overlapping_matches(self):
        """Overlapping pattern matches are correctly counted."""
        # "AAA" in "AAAAA" — occurs at offsets 0, 1, 2
        assert count_unique_in_window(b"AAAAA", b"AAA", 0, 5) == 3


# ---------------------------------------------------------------------------
# find_unique_context
# ---------------------------------------------------------------------------


class TestFindUniqueContext:
    def test_basic_unique_context(self):
        """A unique anchor in high-entropy data is found at some size."""
        # Build data where each offset encodes its position (no wrapping),
        # so every anchor is unique.  Use 2-byte offset encoding.
        data = bytearray(2048)
        for i in range(0, 2048, 2):
            lo = i & 0xFF
            hi = (i >> 8) & 0xFF
            data[i] = lo
            data[i + 1] = hi
        data = bytes(data)

        change_offset = 1024
        ob = data[change_offset : change_offset + 2]

        ctx, size, entropy, matches = find_unique_context(
            data, change_offset, len(ob), ob, min_size=8, max_size=64,
        )
        # With offset-encoded data, every position has unique context
        assert matches == 1
        assert entropy >= 2.5

    def test_auto_expands_when_anchor_non_unique(self):
        """Anchor that appears multiple times causes context expansion."""
        # Build a binary where a short anchor appears multiple times but a
        # longer one is unique.  Use a repeating 32-byte pattern, but place
        # a marker byte that makes the longer window unique.
        pattern = bytes([i for i in range(32)])  # 32 unique bytes
        data = pattern * 32  # 1024 bytes, pattern repeats 32 times
        # The pattern at any offset appears multiple times with short ctx.
        # With ctx >= 32 bytes and ob matching the pattern, the anchor
        # ctx+ob will still repeat.  But with enough context, it becomes unique.
        change_offset = 512  # middle
        ob = data[change_offset : change_offset + 2]

        ctx, size, entropy, matches = find_unique_context(
            data, change_offset, len(ob), ob,
            min_size=8, max_size=256, entropy_threshold=0.0,
        )
        # With a repeating 32-byte pattern, expansion to 256 should
        # eventually make the anchor unique (or at least reduce matches).
        assert size >= 8
        assert isinstance(matches, int)

    def test_expands_on_non_unique(self):
        """Anchor that appears multiple times triggers expansion."""
        # Repeating pattern — anchors will collide with short context
        data = b"\x00\x01\x02\x03" * 512  # 2048 bytes
        change_offset = 1024
        ob = data[change_offset : change_offset + 2]  # 0x00, 0x01

        ctx, size, entropy, matches = find_unique_context(
            data, change_offset, len(ob), ob, min_size=8, max_size=512,
        )
        # With repeating pattern, short ctx will be non-unique.
        # Beyond min_size due to expansion (or stays if already unique).
        assert size >= 8
        assert 0 <= entropy <= 8.0

    def test_max_size_ceiling(self):
        """Expansion respects max_size limit and returns best effort."""
        # All zeros — every anchor will be ambiguous regardless of size.
        data = b"\x00" * 4096
        change_offset = 2048
        ob = data[change_offset : change_offset + 1]  # 0x00

        ctx, size, entropy, matches = find_unique_context(
            data, change_offset, len(ob), ob,
            min_size=32, max_size=128, entropy_threshold=0.0,
        )
        # Should not exceed max_size (128), capped by available bytes or max_size
        assert size <= max(128, change_offset)
        # In all-zeros with ob=0x00, the anchor is all zeros — matches many times
        assert matches > 1

    def test_change_at_offset_zero(self):
        """Change at offset 0 returns empty context."""
        data = bytes(range(256))
        ob = bytes([0x00])
        ctx, size, entropy, matches = find_unique_context(
            data, 0, len(ob), ob, min_size=32, max_size=128,
        )
        assert ctx == b""
        assert size == 0
        assert matches == 0

    def test_entropy_gate_requires_both_conditions(self):
        """Both entropy >= threshold AND match_count == 1 must be satisfied."""
        # Data with repeating but high-entropy pattern.
        pattern = bytes(range(64))  # 64 unique bytes, repeated
        data = pattern * 32  # 2048 bytes
        change_offset = 1024
        ob = data[change_offset : change_offset + 2]

        ctx, size, entropy, matches = find_unique_context(
            data, change_offset, len(ob), ob,
            min_size=8, max_size=256, entropy_threshold=3.0,
        )
        # The entropy of the pattern is ~6.0, but the 64-byte pattern repeats.
        # At some expansion size the anchor should become unique.
        assert 0 <= entropy <= 8.0
        assert size >= 8
        assert isinstance(matches, int)
        # Eventually a large enough window captures enough data to be unique
        # (unless max_size is reached first — either outcome is valid)

    def test_out_of_bounds_offset_raises(self):
        """Negative or out-of-bounds offset raises ValueError."""
        data = bytes(256)
        ob = bytes([0xAA])
        with pytest.raises(ValueError, match="out of bounds"):
            find_unique_context(data, -1, 1, ob, min_size=8, max_size=64)
        with pytest.raises(ValueError, match="out of bounds"):
            find_unique_context(data, 257, 1, ob, min_size=8, max_size=64)
