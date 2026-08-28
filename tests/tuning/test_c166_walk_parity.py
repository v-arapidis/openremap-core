"""
C166 walk parity — Rust decoder vs the corpus-validated nefmoto parser.

The nefmoto Python parser (`_parse_instruction`, `core/services/checksums/
nefmoto.py`) is the size-table seed AND is itself corpus-validated (its ME7
rolling-checksum detection walks real firmware with these sizes and computes
checksums that verify "ok" on a 236-file corpus).  This test walks real
C166 binaries with the **production walker** (Rust `c166.walk`) and, at
every instruction, asks the nefmoto parser what size it would assign.

Expected divergences: nefmoto's table never covered the AND/OR/XOR/CMP-
inc-dec ALU families and the bit ops — the opcodes Ghidra's SLEIGH spec
(and the Rust decoder, oracle-verified) define as 4-byte.  Every divergence
must be one of those spec-verified 4-byte opcodes, with our size 4 and
nefmoto's 2.  The assertion is structural, never a hard count — corpus
content varies per checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openremap.core.arch import c166
from openremap.core.services.checksums.nefmoto import _parse_instruction
from openremap.core.services.maps.layout import code_regions_from_layout, segment
from openremap.core.services.maps.map_hunter import scan_map_tables

_DATA = Path("tests/data")

#: Opcodes the Ghidra SLEIGH spec (mumbel/Ghidra_C166, c166.slaspec)
#: defines as 4-byte that nefmoto's table never covered — the accepted
#: walk divergence set (our 4 vs nefmoto's 2).
_SPEC_4BYTE_OPCODES = frozenset(
    [
        0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0A, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x1A,
        0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x2A, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x3A,
        0x42, 0x43, 0x46, 0x47, 0x4A, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x5A,
        0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x6A, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x7A,
        0x82, 0x84, 0x85, 0x86, 0x87, 0x8A, 0x92, 0x94, 0x96, 0x97, 0x9A,
        0xA2, 0xA4, 0xA5, 0xA6, 0xA7, 0xAA, 0xB2, 0xB4, 0xB5, 0xB6, 0xB7, 0xBA,
        0xC2, 0xC4, 0xC5, 0xC6, 0xCA, 0xD2, 0xD4, 0xD5, 0xD6, 0xD7, 0xDA,
        0xE2, 0xE4, 0xE6, 0xE7, 0xEA, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xFA,
    ]
)

#: Families exercised: one real bin per C166 family present in the corpus.
_C166_BINS = [
    "ECUs/Bosch/ME7",
    "ECUs/Bosch/ME9",
    "ECUs/Bosch/EDC15",
    "ECUs/Bosch/EDC16",
    "ECUs/Siemens/MS43",
    "ECUs/Siemens/PPD1.1",
    "ECUs/Siemens/SID803",
    "ECUs/Siemens/Simtec56",
]


def _has_corpus() -> bool:
    return (_DATA / "ECUs/Bosch/ME7").exists()


def _code_regions(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    return data, code_regions_from_layout(regions)


def _parity(data: bytes, regions: list[tuple[int, int]]) -> tuple[int, list[tuple]]:
    """(agreements, divergences) — Rust walk vs nefmoto sizes.

    The Rust walk is the ruler (production); at each step the nefmoto
    parser reports its size for the same opcode.
    """
    rust_walk = c166.walk(data, [(s, e) for s, e in regions])
    agree = 0
    divergences: list[tuple] = []
    for off, rsize in rust_walk:
        _, nsize = _parse_instruction(data, off)
        if nsize == rsize:
            agree += 1
        else:
            divergences.append((off, data[off], rsize, nsize))
    return agree, divergences


def test_c166_walk_parity_all_families():
    if not _has_corpus():
        pytest.skip("corpus binaries not present")
    total_agree = 0
    total_div = 0
    for rel in _C166_BINS:
        d = _DATA / rel
        bins = sorted(d.glob("*.ori")) + sorted(d.glob("*.bin")) + sorted(d.glob("*.hex"))
        if not bins:
            continue
        data, regions = _code_regions(bins[0])
        if not regions:
            continue  # e.g. EDC16 dumps with no code regions — honest skip
        agree, divergences = _parity(data, regions)
        total_agree += agree
        total_div += len(divergences)
        # structural contract: every divergence is a spec-verified 4-byte
        # opcode where we say 4 and nefmoto says 2 (its table never grew
        # past the checksum-detection subset).
        for off, op, rsize, nsize in divergences:
            assert op in _SPEC_4BYTE_OPCODES, (
                f"{rel} @0x{off:X}: opcode 0x{op:02X} diverges outside the spec set"
            )
            assert rsize == 4 and nsize == 2, (
                f"{rel} @0x{off:X}: expected our 4 / nefmoto 2, got {rsize}/{nsize}"
            )
    if total_agree == 0:
        pytest.skip("no decodable C166 code regions in the corpus")
    # report (visible with -v / on failure)
    print(f"walk-parity: {total_agree} agreements, {total_div} spec-verified 4-byte divergences")
    assert total_div < total_agree, "walk is not mostly agreeing — decode is broken"
