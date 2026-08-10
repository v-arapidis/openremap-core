"""
Oracle fuzz tests — Rust entropy backend vs pure-Python reference.

Every test feeds identical input to both implementations and asserts
bit-identical output.  A single divergence means the Rust port no longer
matches the Python specification and must be fixed before release.

These tests are skipped when the native extension is unavailable (PyPy,
source install without rustup, unsupported platform).  In that case the
pure-Python backend is the only backend, so there is nothing to compare.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Rust backend
# ---------------------------------------------------------------------------

_rs_entropy = pytest.importorskip("openremap._rust").shannon_entropy
_rs_is_low = pytest.importorskip("openremap._rust").is_low_entropy
_rs_count = pytest.importorskip("openremap._rust").count_unique_in_window
_rs_find = pytest.importorskip("openremap._rust").find_unique_context

# ---------------------------------------------------------------------------
# Python reference (always available)
# ---------------------------------------------------------------------------

from openremap.core.services.entropy import (  # noqa: E402
    _py_shannon_entropy as py_entropy,
    _py_is_low_entropy as py_is_low,
    _py_count_unique_in_window as py_count,
    _py_find_unique_context as py_find,
)


# ============================================================================
# shannon_entropy — 200 random byte sequences
# ============================================================================


@pytest.mark.parametrize("seed", range(200))
def test_shannon_entropy_parity(seed: int) -> None:
    """Random byte sequence — Rust and Python must return the same float."""
    import random as _random

    rng = _random.Random(seed)

    length = rng.randint(0, 8192)
    # Occasional all-same-value edge case (every 20th seed)
    if seed % 20 == 0:
        data = bytes([rng.randint(0, 255)] * length)
    else:
        data = bytes(rng.randint(0, 255) for _ in range(length))

    rs = _rs_entropy(data)
    py = py_entropy(data)

    assert rs == py, (
        f"Seed {seed} len={length}: "
        f"Rust={rs}, Python={py}"
    )


# ============================================================================
# is_low_entropy — 50 random (data, threshold) combos
# ============================================================================


@pytest.mark.parametrize("seed", range(50))
def test_is_low_entropy_parity(seed: int) -> None:
    """Rust and Python must agree on the boolean threshold decision."""
    import random as _random

    rng = _random.Random(seed)
    length = rng.randint(1, 2048)
    data = bytes(rng.randint(0, 255) for _ in range(length))
    threshold = round(rng.uniform(0.0, 8.0), 4)

    rs = _rs_is_low(data, threshold)
    py = py_is_low(data, threshold)

    assert rs == py, (
        f"Seed {seed} len={length} threshold={threshold}: "
        f"Rust={rs}, Python={py}"
    )


# ============================================================================
# count_unique_in_window — 50 random (haystack, needle, window) combos
# ============================================================================


@pytest.mark.parametrize("seed", range(50))
def test_count_unique_in_window_parity(seed: int) -> None:
    """Rust and Python must count the same number of occurrences."""
    import random as _random

    rng = _random.Random(seed)
    hay_len = rng.randint(8, 1024)
    haystack = bytes(rng.randint(0, 255) for _ in range(hay_len))

    # Pick a random slice of haystack as the needle
    ndl_start = rng.randint(0, max(0, hay_len - 4))
    ndl_len = rng.randint(1, min(8, hay_len - ndl_start))
    needle = haystack[ndl_start : ndl_start + ndl_len]

    win_start = rng.randint(0, max(0, hay_len // 4))
    win_end = rng.randint(win_start, hay_len)

    rs = _rs_count(haystack, needle, win_start, win_end)
    py = py_count(haystack, needle, win_start, win_end)

    assert rs == py, (
        f"Seed {seed}: Rust={rs}, Python={py}"
    )


# ============================================================================
# find_unique_context — 50 random full-pipeline combos
# ============================================================================


@pytest.mark.parametrize("seed", range(50))
def test_find_unique_context_parity(seed: int) -> None:
    """Full context search — Rust and Python must return identical 4-tuples."""
    import random as _random

    rng = _random.Random(seed)
    size = rng.randint(256, 16384)
    data = bytes(rng.randint(0, 255) for _ in range(size))

    offset = rng.randint(32, size - 4)
    change_size = rng.randint(1, 16)
    ob = data[offset : offset + change_size]

    min_sz = rng.choice([8, 16, 32])
    max_sz = min(min_sz * rng.choice([2, 4, 8, 16]), 512)
    threshold = round(rng.uniform(0.0, 4.0), 4)

    rs_ctx, rs_sz, rs_ent, rs_matches = _rs_find(
        data, offset, change_size, ob, min_sz, max_sz, threshold,
    )
    py_ctx, py_sz, py_ent, py_matches = py_find(
        data, offset, change_size, ob, min_sz, max_sz, threshold,
    )

    assert rs_ctx == py_ctx, (
        f"Seed {seed}: ctx bytes differ — Rust={rs_ctx[:32]!r}..., "
        f"Python={py_ctx[:32]!r}..."
    )
    assert rs_sz == py_sz, (
        f"Seed {seed}: context_size differs — Rust={rs_sz}, Python={py_sz}"
    )
    assert rs_ent == py_ent, (
        f"Seed {seed}: entropy differs — Rust={rs_ent}, Python={py_ent}"
    )
    assert rs_matches == py_matches, (
        f"Seed {seed}: match_count differs — Rust={rs_matches}, "
        f"Python={py_matches}"
    )


# ============================================================================
# Edge cases that must match exactly
# ============================================================================


class TestOracleEdgeCases:
    """Fixed edge cases where divergence is most likely."""

    def test_empty_data(self) -> None:
        assert _rs_entropy(b"") == py_entropy(b"") == 0.0

    def test_single_zero_byte(self) -> None:
        assert _rs_entropy(b"\x00") == py_entropy(b"\x00") == 0.0

    def test_all_256_values(self) -> None:
        data = bytes(range(256))
        assert _rs_entropy(data) == py_entropy(data) == 8.0

    def test_needle_not_found(self) -> None:
        assert _rs_count(b"ABCDEF", b"XY", 0, 6) == py_count(b"ABCDEF", b"XY", 0, 6) == 0

    def test_needle_longer_than_haystack(self) -> None:
        assert _rs_count(b"AB", b"ABCDEF", 0, 2) == py_count(b"AB", b"ABCDEF", 0, 2) == 0

    def test_overlapping_matches(self) -> None:
        # "AAA" in "AAAAA" → offsets 0, 1, 2
        assert _rs_count(b"AAAAA", b"AAA", 0, 5) == py_count(b"AAAAA", b"AAA", 0, 5) == 3

    def test_empty_needle_returns_zero(self) -> None:
        assert _rs_count(b"ABCD", b"", 0, 4) == py_count(b"ABCD", b"", 0, 4) == 0

    def test_change_at_offset_zero(self) -> None:
        data = bytes(range(256))
        ob = bytes([0x00])
        rs = _rs_find(data, 0, 1, ob, 32, 128, 2.5)
        py = py_find(data, 0, 1, ob, 32, 128, 2.5)
        assert rs == py == (b"", 0, 0.0, 0)

    def test_out_of_bounds_offset(self) -> None:
        data = bytes(256)
        ob = bytes([0xAA])
        with pytest.raises(Exception):
            _rs_find(data, -1, 1, ob, 8, 64, 2.5)
        with pytest.raises(Exception):
            py_find(data, -1, 1, ob, 8, 64, 2.5)

    def test_max_size_ceiling_all_zeros(self) -> None:
        """All-zeros binary — both backends must return the same best-effort."""
        data = b"\x00" * 2048
        ob = b"\x00"
        rs = _rs_find(data, 1024, 1, ob, 32, 128, 0.0)
        py = py_find(data, 1024, 1, ob, 32, 128, 0.0)
        assert rs[0] == py[0]  # context bytes equal
        assert rs[1] == py[1]  # context size equal
        assert rs[2] == py[2]  # entropy equal
        assert rs[3] == py[3]  # match_count equal (both > 1)
        assert rs[3] > 1
