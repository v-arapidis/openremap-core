"""
TriCore a0-resolution — unit tests (cheap-wins item 4, 2026-08-27).

Hand-encoded TriCore instruction streams exercising:
- the boot-time ``movh.a a0, #hi`` + ``lea a0, [a0]disp`` init pair
  (:func:`_find_a0`, the a0 branch of ``_extract_tricore_pass``);
- ``lea aN, [a0]disp`` resolution against the base
  (:func:`_extract_tricore_a0`) — including negative displacements and
  32-bit masking;
- the presence-only contract: no a0 init found → nothing extra is emitted.

The byte encodings are captured from the real EDC17 corpus and verified
through capstone rendering, so the tests exercise the exact production
decode path (``detail=False`` rendered-operand parsing) — no
hand-assembled encodings.

Real encodings used (mnemonic → bytes):
- movh.a a0, #0xd001  → 91 10 00 0d
- lea a0, [a0]#-0x7800 → d9 00 80 08
- lea a0, [a0]#-0x7f80 → d9 00 00 28
- movh.a a1, #0x8003  → 91 30 00 18
- lea a1, [a1]#-0x118c → d9 11 f4 9e
- lea a7, [a0]#0x5f40 → d9 07 c0 d5
- lea a15, [a0]#-0x4c4 → d9 0f bc cf
- ret                → 00 90
"""

from __future__ import annotations

from capstone import CS_ARCH_TRICORE, Cs

from openremap.core.arch.refs import collect_xrefs
from openremap.core.arch.tricore import (
    _extract_tricore,
    _extract_tricore_a0,
    _extract_tricore_pass,
    _find_a0,
)

#: movh.a a0, #0xd001 — high word of the boot-time base
_MOVHA_A0 = bytes.fromhex("9110000d")
#: lea a0, [a0]#-0x7800 → base 0xd0008800
_LEA_A0_INIT_8800 = bytes.fromhex("d9008008")
#: lea a0, [a0]#-0x7f80 → base 0xd0008080 (a second, non-canonical init)
_LEA_A0_INIT_8080 = bytes.fromhex("d9000028")
#: movh.a a1, #0x8003 / lea a1, [a1]#-0x118c → 0x8002ee74 (self-contained)
_MOVHA_A1 = bytes.fromhex("91300018")
_LEA_A1_SELF = bytes.fromhex("d911f49e")
#: lea a7, [a0]#0x5f40 / lea a15, [a0]#-0x4c4 (a0-relative accesses)
_LEA_A7_A0 = bytes.fromhex("d907c0d5")
_LEA_A15_A0 = bytes.fromhex("d90fbccf")
_RET = bytes.fromhex("0090")


def _disasm(code: bytes, base: int = 0) -> list:
    """Decode *code* exactly like the collector (skipdata on, linear)."""
    md = Cs(CS_ARCH_TRICORE, 0)
    md.skipdata = True
    return list(md.disasm(code, base))


def _tricore_arch() -> tuple:
    return ("tricore", CS_ARCH_TRICORE, 0, False)


# ---------------------------------------------------------------------------
# _find_a0
# ---------------------------------------------------------------------------


def test_find_a0_returns_boot_base():
    code = _MOVHA_A0 + _LEA_A0_INIT_8800
    assert _find_a0(_disasm(code)) == 0xD0008800


def test_find_a0_survives_leading_code():
    # Unrelated instructions before the init must not disturb the walk.
    code = (
        _RET
        + _MOVHA_A1
        + _LEA_A1_SELF
        + _MOVHA_A0
        + _LEA_A0_INIT_8800
    )
    assert _find_a0(_disasm(code)) == 0xD0008800


def test_find_a0_none_without_init():
    # Only a self-contained a1 pair — a0 is never initialised.
    code = _MOVHA_A1 + _LEA_A1_SELF
    assert _find_a0(_disasm(code)) is None


def test_find_a0_empty_stream():
    assert _find_a0(_disasm(b"")) is None


def test_find_a0_first_pair_wins():
    # The init is one-shot boot code; the first canonical pair is the base.
    code = (
        _MOVHA_A0 + _LEA_A0_INIT_8800 + _RET
        + _MOVHA_A0 + _LEA_A0_INIT_8080
    )
    assert _find_a0(_disasm(code)) == 0xD0008800


# ---------------------------------------------------------------------------
# _extract_tricore_a0
# ---------------------------------------------------------------------------


def test_extract_tricore_a0_positive_and_negative_disp():
    code = _LEA_A7_A0 + _LEA_A15_A0
    out = _extract_tricore_a0(_disasm(code), 0xD0008800)
    # 0xd0008800 + 0x5f40 = 0xd000e740 ; 0xd0008800 - 0x4c4 = 0xd000833c
    assert out == [(0xD000E740, 0), (0xD000833C, 4)]


def test_extract_tricore_a0_excludes_self_init():
    # lea a0, [a0] is the init pair's own half — aN == a0 → not a target.
    assert _extract_tricore_a0(_disasm(_LEA_A0_INIT_8800), 0xD0008800) == []


def test_extract_tricore_a0_ignores_non_a0_base():
    # lea a1, [a1]#-0x118c — base register is a1, not a0.
    assert _extract_tricore_a0(_disasm(_LEA_A1_SELF), 0xD0008800) == []


def test_extract_tricore_a0_masks_32_bits():
    # A displacement that carries the sum past 2^32 must wrap.
    code = _LEA_A7_A0  # disp 0x5f40
    assert _extract_tricore_a0(_disasm(code), 0xFFFFFFFF) == [
        (0x5F3F, 0)
    ]  # (0xFFFFFFFF + 0x5f40) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# _extract_tricore_pass — single-walk buffering
# ---------------------------------------------------------------------------


def test_extract_tricore_pass_pairs_base_and_buffer():
    code = (
        _MOVHA_A0 + _LEA_A0_INIT_8800  # a0 init → 0xd0008800 (offsets 0, 4)
        + _MOVHA_A1 + _LEA_A1_SELF  # self-contained a1 pair → 0x8002ee74 (8, 12)
        + _LEA_A7_A0  # a0-relative lea (16)
    )
    pairs, a0_base, leas = _extract_tricore_pass(_disasm(code))
    assert a0_base == 0xD0008800
    # the init pair is itself a self-contained pair…
    assert (0xD0008800, 0) in pairs
    # …and the a1 pair is unchanged (existing signal intact)
    assert (0x8002EE74, 8) in pairs
    # the [a0] lea is buffered, not resolved by the pass
    assert len(leas) == 1
    assert _extract_tricore_a0(leas, a0_base) == [(0xD000E740, 16)]


def test_extract_tricore_pass_no_a0_buffers_but_no_base():
    code = _MOVHA_A1 + _LEA_A1_SELF + _LEA_A7_A0
    pairs, a0_base, leas = _extract_tricore_pass(_disasm(code))
    assert a0_base is None
    assert len(leas) == 1  # buffered, but nothing to resolve against
    assert (0x8002EE74, 0) in pairs


def test_extract_tricore_matches_pass_pairs():
    # The standalone extractor agrees with the pass's pair output.
    code = _MOVHA_A0 + _LEA_A0_INIT_8800 + _MOVHA_A1 + _LEA_A1_SELF
    pairs, _, _ = _extract_tricore_pass(_disasm(code))
    assert _extract_tricore(_disasm(code)) == pairs


# ---------------------------------------------------------------------------
# collect_xrefs — end-to-end wiring (presence-only contract)
# ---------------------------------------------------------------------------


def test_collect_xrefs_tricore_a0_end_to_end():
    code = (
        _MOVHA_A0 + _LEA_A0_INIT_8800  # a0 init → 0xd0008800
        + _MOVHA_A1 + _LEA_A1_SELF  # a1 pair → 0x8002ee74 (out of file here)
        + _LEA_A7_A0  # → 0xd000e740
        + _LEA_A15_A0  # → 0xd000833c
    )
    data = code + bytes(0x10000 - len(code))
    xr = collect_xrefs(
        data, [(0, len(code))], _tricore_arch(), "big", base_address=0xD0000000
    )
    assert xr.status == "ok"
    assert xr.base_address == 0xD0000000
    # a0 init → file 0x8800; a0 leas → file 0xe740 / 0x833c;
    # the a1 pair (0x8002ee74) is below the window → discarded.
    assert xr.referenced == {0x8800, 0xE740, 0x833C}
    assert xr.refs[0x8800] == (0,)  # from the init pair's lea
    assert xr.refs[0xE740] == (16,)
    assert xr.refs[0x833C] == (20,)


def test_collect_xrefs_tricore_no_a0_emits_nothing_extra():
    # lea aN,[a0] present but NO a0 init → the a0 accesses must not
    # produce anything (presence-only: a missing signal is never a bonus).
    code = _MOVHA_A1 + _LEA_A1_SELF + _LEA_A7_A0
    data = code + bytes(0x30000 - len(code))  # ≥ 0x8002ee74 - 0x80000000
    xr = collect_xrefs(
        data, [(0, len(code))], _tricore_arch(), "big", base_address=0x80000000
    )
    assert xr.status == "ok"
    # only the self-contained a1 pair target (0x8002ee74 → file 0x2ee74)
    assert xr.referenced == {0x2EE74}
    assert xr.refs[0x2EE74] == (0,)
