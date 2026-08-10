"""Oracle fuzz tests — Rust diff backend vs pure-Python reference."""

from __future__ import annotations

import pytest

_rs_find = pytest.importorskip("openremap._rust").find_changed_blocks

from openremap.core.services.recipe_builder import (  # noqa: E402
    _py_find_changed_blocks as py_find,
)


@pytest.mark.parametrize("seed", range(100))
def test_find_changed_blocks_parity(seed: int) -> None:
    import random as _random

    rng = _random.Random(seed)
    size = rng.randint(1, 65536)

    orig = bytes(rng.randint(0, 255) for _ in range(size))
    mod = bytearray(orig)

    # Flip 0–50 random bytes
    num_changes = rng.randint(0, 50)
    for _ in range(num_changes):
        pos = rng.randint(0, size - 1)
        mod[pos] = (mod[pos] + rng.randint(1, 255)) & 0xFF

    mod = bytes(mod)
    threshold = rng.choice([1, 4, 8, 16, 32])

    rs = _rs_find(orig, mod, threshold)
    py = py_find(orig, mod, threshold)

    assert rs == py, f"Seed {seed}: Rust={rs!r}, Python={py!r}"


class TestDiffEdgeCases:
    def test_identical_binaries(self) -> None:
        data = bytes(range(256))
        assert _rs_find(data, data, 16) == py_find(data, data, 16) == []

    def test_single_byte_change(self) -> None:
        orig = bytes(100)
        mod = bytearray(orig)
        mod[50] = 0xFF
        rs = _rs_find(orig, bytes(mod), 16)
        py = py_find(orig, bytes(mod), 16)
        assert rs == py
        assert rs == [(50, 1, b"\x00", b"\xff")]

    def test_adjacent_changes_merged(self) -> None:
        """Bytes at 50 and 53 differ — gap of 2 bytes should merge with threshold=4."""
        orig = bytes(100)
        mod = bytearray(orig)
        mod[50] = 0xFF
        mod[53] = 0xAA
        rs = _rs_find(orig, bytes(mod), 4)
        py = py_find(orig, bytes(mod), 4)
        assert rs == py
        assert rs[0][0] == 50   # offset
        assert rs[0][1] == 4    # size (50..53 inclusive)

    def test_distant_changes_not_merged(self) -> None:
        """Bytes at 10 and 90 differ — gap of 79 bytes should NOT merge."""
        orig = bytes(100)
        mod = bytearray(orig)
        mod[10] = 0xFF
        mod[90] = 0xAA
        rs = _rs_find(orig, bytes(mod), 16)
        py = py_find(orig, bytes(mod), 16)
        assert rs == py
        assert len(rs) == 2

    def test_different_lengths_clamped(self) -> None:
        """Only the overlapping region is compared."""
        orig = bytes(100)
        mod = bytes(80)
        rs = _rs_find(orig, mod, 16)
        py = py_find(orig, mod, 16)
        assert rs == py

    def test_empty_binaries(self) -> None:
        assert _rs_find(b"", b"", 16) == py_find(b"", b"", 16) == []
