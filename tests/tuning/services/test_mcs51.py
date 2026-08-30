"""MCS-51 (8051) decoder — Rust adapter + ``collect_xrefs`` branch tests.

The Rust decoder (``openremap._rust.mcs51_references`` / ``mcs51_walk``,
``_rs/src/arch/mcs51.rs``) and the identity-mapped ``collect_xrefs`` 8051
branch (``core/arch/refs.py::_collect_mcs51``) cover the 8051 families —
M2.x / MP9 / M4.x / SIMOS / Simtec56 / M1.8 (census §6-C).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import arch_for_family, mcs51
from openremap.core.arch.refs import collect_xrefs

_8051 = ("8051", 0, 0, False)


def test_mcs51_walk_aligns_on_ljmp_vector():
    # LJMP 0x0F00 = 02 0F 00 (3 bytes) at 0x00 and 0x03.
    d = bytes([0x02, 0x0F, 0x00, 0x02, 0x0E, 0x00])
    assert mcs51.walk(d, [(0, 6)]) == [(0, 3), (3, 3)]


def test_mcs51_references_collects_mov_dptr():
    # MOV DPTR, #0x1234 = 90 12 34 (big-endian); MOV A,#0x12 (74 12) is NOT
    # a 16-bit reference — only MOV DPTR,#data16 is.
    d = bytes([0x90, 0x12, 0x34, 0x74, 0x12, 0x90, 0xAB, 0xCD])
    refs, insns = mcs51.collect_references(d, [(0, 8)])
    assert refs == [(0x1234, 0), (0xABCD, 5)]
    assert insns == 3


def test_collect_xrefs_8051_ok():
    # MOV DPTR, #0x0080 → reference 0x0080 (identity-mapped).
    d = bytes([0x90, 0x00, 0x80]) + bytes(0x1FFD)
    xr = collect_xrefs(d, [(0, len(d))], _8051, "little", spans=[(0x0080, 0x0090)])
    assert xr.status == "ok"
    assert xr.arch == "8051"
    assert 0x0080 in xr.referenced


def test_arch_for_family_8051_families():
    for fam in ("M1.8", "M2.9", "MP9", "M4.3"):
        info = arch_for_family("Bosch", fam)
        assert info is not None and info[0] == "8051", fam
    for fam in ("SIMOS", "Simtec56"):
        info = arch_for_family("Siemens", fam)
        assert info is not None and info[0] == "8051", fam


def test_mcs51_corpus_signal_fires():
    # Real M2.9 (8051) — the decoder must produce DPTR references.
    p = Path("tests/data/ECUs/Bosch/M2.9/0261203219_soft109__1__1.bin")
    if not p.exists():
        pytest.skip("M2.9 corpus absent")
    data = p.read_bytes()
    refs, insns = mcs51.collect_references(data, [(0, len(data))])
    assert insns > 1000
    assert len(refs) > 100
