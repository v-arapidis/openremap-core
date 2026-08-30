"""
Pseudo-code rendering (Phase B) — the phrasebook front-end.

Corpus-gated tests skip cleanly when ``tests/data/`` is absent; the
synthetic c166-wrapper test always runs (the Rust decoder is mandatory,
not corpus-dependent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import c166
from openremap.core.arch.pseudocode import render_routine

_DATA = Path("tests/data")


def _first_bin(rel: str) -> Path | None:
    d = _DATA / rel
    bins = sorted(d.glob("*.ori")) + sorted(d.glob("*.bin"))
    return bins[0] if bins else None


def test_c166_disasm_wrapper_renders_forms():
    # MOV R0, 0x1234 (F2 F0 34 12) + ADD R5, #0x0006 (06 F5 06 00)
    d = bytes.fromhex("F2F03412" "06F50600")
    out = c166.disasm(d, [(0, 8)])
    assert out == [
        (0, 4, "MOV", "R0, 0x1234"),
        (4, 4, "ADD", "R5, #0x0006"),
    ]


def test_render_routine_no_arch_is_a_hint():
    lines = render_routine(b"\x00" * 64, 0, arch=None)
    assert len(lines) == 1
    assert lines[0].startswith(";; arch not specified")


def test_render_c166_corpus():
    path = _first_bin("ECUs/Bosch/ME7.5") or _first_bin("ECUs/Bosch/ME7")
    if path is None:
        pytest.skip("no ME7 corpus files present")
    data = path.read_bytes()
    # the first code region starts at 0; offset 0x100 sits inside it
    lines = render_routine(data, 0x100, arch="c166")
    assert lines, "expected rendered instructions"
    # one line is marked as the target
    assert any(l.startswith(">> ") for l in lines)
    # every line carries an address and a mnemonic-ish token
    for line in lines:
        if line.startswith(";;"):
            continue
        assert line[0:2] in ("  ", ">>")
        assert "  " in line


def test_render_tricore_corpus():
    path = _first_bin("ECUs/Bosch/EDC17")
    if path is None:
        pytest.skip("no EDC17 corpus files present")
    data = path.read_bytes()
    lines = render_routine(data, 0x100, arch="tricore")
    assert lines, "expected rendered instructions"
    assert any(l.startswith(">> ") for l in lines)


def test_render_m680x_corpus():
    path = _first_bin("ECUs/Bosch/M1.3") or _first_bin("ECUs/Bosch/M3.1")
    if path is None:
        pytest.skip("no M1.x/M3.x corpus files present")
    data = path.read_bytes()
    lines = render_routine(data, 0x100, arch="m680x")
    assert lines, "expected rendered instructions"
    assert any(l.startswith(">> ") for l in lines)
