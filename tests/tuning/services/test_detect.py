"""
CPU-detection cascade (``core/arch/detect.py``) — unit + corpus-gated tests.

The unit tests use the private ``candidates=`` hook with **x86** (hand-
encodable, see ``test_xrefs.py`` §"capstone mechanics") to exercise the
gate logic deterministically — no TriCore/SH/C166 encodings needed.  The
crash-isolation test pins the forked-trial behaviour against capstone's
SH-2A decoder segfault on garbage bytes (a C-level crash Python cannot
catch — the cascade must reject the candidate, not die).

Corpus-gated tests (skip when ``tests/data/`` absent) assert soft
structural behaviour only — never a hard arch claim (SIMOS 2.x vs 3.x CPU
is not verified): no crash, JSON round-trips, and either a detection
(arch in {c166, tricore, sh}) or a clean skip.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest
from capstone import (
    CS_ARCH_SH,
    CS_ARCH_TRICORE,
    CS_ARCH_X86,
    CS_MODE_SH2,
    CS_MODE_SH2A,
    CS_MODE_32,
)

from openremap.core.arch import arch_for_family
from openremap.core.arch.detect import (
    _CASCADE_CANDIDATES,
    _MIN_INSNS,
    _MIN_SPAN_HITS,
    _accepts,
    _trial_collect,
    detect_arch,
)
from openremap.core.arch.refs import XrefReport

# ---------------------------------------------------------------------------
# Helpers — hand-encoded x86 (absolute-addressing mov eax, [imm32])
# ---------------------------------------------------------------------------

#: mov eax, [0x1234] (A1 imm32) — 3 distinct offsets inside the span below.
_GOOD_OFFSETS = (0x1234, 0x1238, 0x123C)
_GOOD_SPANS = [(0x1234, 0x1240)]

#: capstone's SH-2A decoder segfaults (C-level) on this 4-byte sequence —
#: verified on capstone 5.0.x (lock-pinned) with the real corpus.
_SH2A_CRASH_BYTES = bytes.fromhex("3351e531")


def _x86_arch() -> tuple:
    return ("x86", CS_ARCH_X86, CS_MODE_32, False)


def _tricore_arch() -> tuple:
    # A real decoder that, over x86 code bytes, yields no absolute refs —
    # the "bad" candidate for order tests.
    return ("tricore", CS_ARCH_TRICORE, 0, False)


def _mov_abs(offset: int) -> bytes:
    return b"\xA1" + struct.pack("<I", offset)


def _x86_code(offsets, reps: int = 20) -> bytes:
    """60 valid instructions referencing *offsets* (reps=20 each) — clears
    the ``_MIN_INSNS`` (50) gate."""
    return b"".join(_mov_abs(o) * reps for o in offsets)


def _padded(data: bytes, size: int = 0x2000) -> bytes:
    return data + bytes(size - len(data))


def _xr(**kw) -> XrefReport:
    base = dict(
        status="ok", skip_reason=None, arch="x86", endian="little",
        base_address=0, code_bytes_scanned=100, insn_count=50,
        referenced=frozenset(), refs={},
    )
    base.update(kw)
    return XrefReport(**base)


# ---------------------------------------------------------------------------
# detect_arch — skip paths
# ---------------------------------------------------------------------------


def test_detect_arch_skips_without_code_regions():
    xr = detect_arch(b"\x00" * 256, [], "little", _GOOD_SPANS)
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_code_regions"
    assert xr.arch is None and xr.refs == {}


def test_detect_arch_skips_without_spans():
    xr = detect_arch(_padded(_x86_code(_GOOD_OFFSETS)), [(0, 300)], "little", [])
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_arch_detected"  # nothing to gate on


# ---------------------------------------------------------------------------
# detect_arch — gate logic via the private candidates= hook (x86)
# ---------------------------------------------------------------------------


def test_detect_arch_accepts_injected_x86():
    data = _padded(_x86_code(_GOOD_OFFSETS))
    xr = detect_arch(
        data, [(0, 300)], "little", _GOOD_SPANS,
        candidates=[_x86_arch()],
    )
    assert xr.status == "ok"
    assert xr.arch == "x86"
    assert xr.base_address == 0
    assert xr.referenced == frozenset(_GOOD_OFFSETS)


def test_detect_arch_rejects_candidate_without_span_hits():
    # 100 nops — decodes fine (status ok, insn_count 100) but zero
    # references inside the spans → not accepted.
    data = _padded(b"\x90" * 100)
    xr = detect_arch(
        data, [(0, 100)], "little", _GOOD_SPANS,
        candidates=[_x86_arch()],
    )
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_arch_detected"


def test_detect_arch_rejects_candidate_below_insn_gate():
    # Only 3 instructions — below _MIN_INSNS even though they hit spans.
    data = _padded(b"".join(_mov_abs(o) for o in _GOOD_OFFSETS))
    xr = detect_arch(
        data, [(0, 15)], "little", _GOOD_SPANS,
        candidates=[_x86_arch()],
    )
    assert xr.status == "skipped"
    assert xr.skip_reason == "no_arch_detected"


def test_detect_arch_first_match_wins():
    # The cascade returns the FIRST acceptable candidate's report.  Over
    # x86 code bytes the tricore decoder yields no span hits, so it is the
    # "bad" candidate: [good, bad] → good wins; [bad, good] → the failed
    # first candidate is skipped and good still wins.
    data = _padded(_x86_code(_GOOD_OFFSETS))
    xr = detect_arch(
        data, [(0, 300)], "little", _GOOD_SPANS,
        candidates=[_x86_arch(), _tricore_arch()],
    )
    assert xr.status == "ok"
    assert xr.arch == "x86"
    assert xr.referenced == frozenset(_GOOD_OFFSETS)
    xr = detect_arch(
        data, [(0, 300)], "little", _GOOD_SPANS,
        candidates=[_tricore_arch(), _x86_arch()],
    )
    assert xr.status == "ok"
    assert xr.arch == "x86"
    assert xr.referenced == frozenset(_GOOD_OFFSETS)


# ---------------------------------------------------------------------------
# Crash isolation — capstone C-level crashes must reject, never kill
# ---------------------------------------------------------------------------


def test_trial_collect_survives_crashing_decoder():
    """A misbehaving decoder must not kill the parent: capstone's SH-2A
    decoder has a known out-of-bounds read on crafted bytecode (GHSA-
    gf2c-xwcp-hvf4 / CVE-2026-55894, e.g. ``3351e531``) — an OOB read
    usually segfaults the child (→ trial yields ``None``, candidate
    rejected) but is not *guaranteed* to fault, so the test asserts the
    invariant that matters: the process survives the trial and a
    subsequent normal trial still round-trips."""
    if not hasattr(os, "fork"):
        pytest.skip("crash isolation requires os.fork")
    sh2a = ("sh", CS_ARCH_SH, CS_MODE_SH2A, True)
    crash = _SH2A_CRASH_BYTES * 16
    xr = _trial_collect(crash, [(0, len(crash))], sh2a, "big", [])
    # either the child died (None → rejected) or the OOB read happened not
    # to fault this run (a report) — the parent is demonstrably alive
    # because the next trial round-trips.
    zeros = b"\x00" * 256
    xr2 = _trial_collect(zeros, [(0, len(zeros))], sh2a, "big", [])
    assert xr2 is not None
    assert xr2.status == "ok"


def test_detect_arch_crashing_candidate_falls_through():
    """A crashing SH-2A candidate must not abort detect_arch — the next
    candidate is tried and wins."""
    if not hasattr(os, "fork"):
        pytest.skip("crash isolation requires os.fork")
    # Data whose first 16 bytes crash the SH-2A decoder and whose remainder
    # decodes under x86 as mov eax,[0x1234/0x1238/0x123C] (inside the spans).
    data = _padded(_SH2A_CRASH_BYTES * 4 + _x86_code(_GOOD_OFFSETS))
    sh2a = ("sh", CS_ARCH_SH, CS_MODE_SH2A, True)
    xr = detect_arch(
        data, [(0, 316)], "big", _GOOD_SPANS,
        candidates=[sh2a, _x86_arch()],
    )
    assert xr.status == "ok"
    assert xr.arch == "x86"
    assert xr.referenced == frozenset(_GOOD_OFFSETS)


# ---------------------------------------------------------------------------
# _accepts gate
# ---------------------------------------------------------------------------


def test_accepts_rejects_non_ok():
    assert _accepts(_xr(status="skipped", skip_reason="x"), _GOOD_SPANS) is False


def test_accepts_rejects_below_insn_gate():
    xr = _xr(insn_count=_MIN_INSNS - 1, referenced=frozenset(_GOOD_OFFSETS))
    assert _accepts(xr, _GOOD_SPANS) is False


def test_accepts_rejects_zero_span_hits():
    xr = _xr(referenced=frozenset({0x5000, 0x5004, 0x5008}))
    assert _accepts(xr, _GOOD_SPANS) is False


def test_accepts_rejects_below_span_hit_bar():
    xr = _xr(referenced=frozenset(_GOOD_OFFSETS[:2]))  # 2 < _MIN_SPAN_HITS
    assert _accepts(xr, _GOOD_SPANS) is False


def test_accepts_passes_at_the_bar():
    xr = _xr(referenced=frozenset(_GOOD_OFFSETS))  # 3 hits == _MIN_SPAN_HITS
    assert _accepts(xr, _GOOD_SPANS) is True


# ---------------------------------------------------------------------------
# Candidate order / table untouched
# ---------------------------------------------------------------------------


def test_cascade_candidates_order_and_no_x86():
    # c166 (fastest/most likely) → TriCore → SuperH SH-2 → SH-2A; x86 is a
    # test-only fallback and must stay out of the production cascade.
    assert [c[0] for c in _CASCADE_CANDIDATES] == ["c166", "tricore", "sh", "sh"]
    assert all(c[0] != "x86" for c in _CASCADE_CANDIDATES)
    assert _CASCADE_CANDIDATES[2][2] == CS_MODE_SH2  # SH-2 base mode
    assert _CASCADE_CANDIDATES[3][2] == CS_MODE_SH2A  # SH-2A base mode


def test_arch_for_family_table_untouched():
    # Regression guard for the "cascade is a separate fallback, the table
    # is NOT modified" contract.
    assert arch_for_family("Siemens", "SIMOS") is None
    assert arch_for_family("Bosch", "M1.3") is None
    assert arch_for_family("Bosch", "EDC17") is not None  # table still works


# ---------------------------------------------------------------------------
# Corpus-gated — soft structural assertions only
# ---------------------------------------------------------------------------

_DATA = Path("tests/data")
_SIMOS = _DATA / "ECUs/Siemens/SIMOS"
_M13 = _DATA / "ECUs/Bosch/M1.3"
_ALLOWED_ARCH = {"c166", "tricore", "sh"}
_ALLOWED_SKIPS = {"no_arch_detected", "no_code_regions"}


def _assert_soft_outcome(xr) -> None:
    """No crash + either a detection (arch in the cascade set) or a clean
    skip — presence-only, so absence is never an error."""
    assert xr is not None
    if xr.status == "ok":
        assert xr.arch in _ALLOWED_ARCH
    else:
        assert xr.status == "skipped"
        assert xr.skip_reason in _ALLOWED_SKIPS


def test_simos_cascade_detects_or_skips_cleanly():
    if not _SIMOS.is_dir():
        pytest.skip("SIMOS corpus absent")
    bins = sorted(_SIMOS.glob("*.bin"))
    if not bins:
        pytest.skip("no SIMOS .bin files present")
    from openremap.core.arch.detect import detect_arch
    from openremap.core.services.identify.identifier import identify_ecu
    from openremap.core.services.maps.layout import code_regions_from_layout, segment
    from openremap.core.services.maps.map_hunter import scan_map_axes, scan_map_tables
    from openremap.core.services.maps.xrefs import _table_spans

    data = bins[0].read_bytes()
    ident = identify_ecu(data, bins[0].name)
    assert arch_for_family(ident.get("manufacturer"), ident.get("ecu_family")) is None
    tables = scan_map_tables(data, axes=scan_map_axes(data))
    codes = code_regions_from_layout(segment(data, tables=tables))
    spans = _table_spans(tables)
    xr = detect_arch(data, codes, ident.get("ecu_endian"), spans)
    _assert_soft_outcome(xr)


def test_simos_analyze_end_to_end_no_crash_json_roundtrip():
    if not _SIMOS.is_dir():
        pytest.skip("SIMOS corpus absent")
    bins = sorted(_SIMOS.glob("*.bin"))
    if not bins:
        pytest.skip("no SIMOS .bin files present")
    from openremap.core.services.analyze import analyze_binary

    data = bins[0].read_bytes()
    report = analyze_binary(
        data, bins[0].name, skip_maps=False, fast=False, container="raw binary",
    )
    _assert_soft_outcome(report.xrefs)
    d = report.to_dict()
    json.dumps(d)  # must round-trip
    assert d["xrefs"]["status"] in {"ok", "skipped"}


def test_bosch_legacy_cascade_no_crash():
    if not _M13.is_dir():
        pytest.skip("Bosch M1.3 corpus absent")
    bins = sorted(_M13.glob("*.bin"))
    if not bins:
        pytest.skip("no Bosch M1.3 .bin files present")
    from openremap.core.services.analyze import analyze_binary

    data = bins[0].read_bytes()
    report = analyze_binary(
        data, bins[0].name, skip_maps=False, fast=False, container="raw binary",
    )
    _assert_soft_outcome(report.xrefs)
    json.dumps(report.to_dict())
