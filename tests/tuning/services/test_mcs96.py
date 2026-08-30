"""MCS-96 (8096) decoder — Rust adapter + ``collect_xrefs`` branch tests.

The Rust decoder (``openremap._rust.mcs96_references`` / ``mcs96_walk``,
``_rs/src/arch/mcs96.rs``) and the identity-mapped ``collect_xrefs`` mcs96
branch cover the EDC1 (8096) family — census §6-C.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import arch_for_family, mcs96
from openremap.core.arch.refs import collect_xrefs

_MCS96 = ("mcs96", 0, 0, False)


def test_mcs96_walk_known_forms():
    # NOT (0x02 0x46) = 2; RST (0xFF) = 1; LJMP (0xE7 xx xx) = 3.
    d = bytes([0x02, 0x46, 0xFF, 0xE7, 0x78, 0x56])
    assert mcs96.walk(d, [(0, 6)]) == [(0, 2), (2, 1), (3, 3)]


def test_mcs96_references_collects_ld_and_jumps():
    # LD reg,#0x1234 (A1 34 12 1C) → 0x1234; LJMP 0x5678 (E7 78 56) → 0x5678.
    d = bytes([0xA1, 0x34, 0x12, 0x1C, 0xE7, 0x78, 0x56])
    refs, insns = mcs96.collect_references(d, [(0, 7)])
    assert refs == [(0x1234, 0), (0x5678, 4)]
    assert insns == 2


def test_collect_xrefs_mcs96_ok():
    # LJMP 0x0080 (E7 80 00) → reference 0x0080 (identity-mapped).
    d = bytes([0xE7, 0x80, 0x00]) + bytes(0x1FFD)
    xr = collect_xrefs(d, [(0, len(d))], _MCS96, "little")
    assert xr.status == "ok"
    assert xr.arch == "mcs96"
    assert 0x0080 in xr.referenced


def test_arch_for_family_edc1_mcs96_no_prefix_collision():
    # EDC1 → mcs96; EDC15/EDC16/EDC17 must NOT be shadowed by the "EDC1"
    # prefix (they are checked first in the table).
    assert arch_for_family("Bosch", "EDC1")[0] == "mcs96"
    assert arch_for_family("Bosch", "EDC15")[0] == "c166"
    assert arch_for_family("Bosch", "EDC16")[0] == "tricore"
    assert arch_for_family("Bosch", "EDC17")[0] == "tricore"


def test_arch_for_family_edc3_mcs96():
    # EDC3 = MCS-96 (8096) — confirmed via Ghidra MCS96 on the corpus.
    assert arch_for_family("Bosch", "EDC3")[0] == "mcs96"


def test_mcs96_corpus_signal_fires():
    p = Path("tests/data/ECUs/Bosch/EDC1/0281001214 2537355342 __1__1.Ori")
    if not p.exists():
        pytest.skip("EDC1 corpus absent")
    data = p.read_bytes()
    refs, insns = mcs96.collect_references(data, [(0, len(data))])
    assert insns > 1000
    assert len(refs) > 0
