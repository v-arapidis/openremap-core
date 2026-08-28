"""
Coherence check — identity / checksum / xref cross-checks (unit + corpus).

Unit tests fabricate ``identity`` dicts, checksum summaries, and
``XrefReport``s (the ``_xr`` helper pattern from ``test_xrefs.py``) to
exercise every rule in ``notes/state/coherence-check-plan.md`` §3:

- identity unknown + checksum fired → gap (not a conflict);
- checksum family == identity family, verifies → agree;
- checksum family == identity family, ``ok != total`` → stale;
- checksum family ≠ identity family (verified) → conflict;
- no checksum family → gap;
- arch matches ``arch_for_family`` → agree; skipped/None → gap;
  cascade-detected arch on an unmapped family → gap (not a conflict);
- ``score_identity(coherence=...)``: agree → +10, conflict → -15 +
  "SIGNAL CONFLICT" warning, stale → warning only, None → unchanged;
- ``CoherenceReport.to_dict()`` is JSON-safe.

Corpus-gated tests (skip when ``tests/data/`` is absent — CI never has
it): real MS43 base → agree (never conflict); real MS43 ``*_mod_*`` →
checksum stale, never conflict; a real unknown-family bin → clean
gap/cascade, never a crash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openremap.core.arch.refs import XrefReport
from openremap.core.services.coherence import (
    CoherenceReport,
    _AGREE_BONUS,
    _CONFLICT_PENALTY,
    check_coherence,
)
from openremap.core.services.identify.confidence import score_identity

# ---------------------------------------------------------------------------
# Helpers (fabricated inputs — no corpus needed)
# ---------------------------------------------------------------------------


def _xr(status="ok", arch="c166", skip_reason=None) -> XrefReport:
    """Minimal XrefReport (pattern borrowed from test_xrefs.py)."""
    return XrefReport(
        status=status,
        skip_reason=skip_reason,
        arch=arch,
        endian="little",
        base_address=0,
        code_bytes_scanned=1000,
        insn_count=50,
        referenced=frozenset(),
        refs={},
    )


def _ident(family=None, manufacturer=None) -> dict:
    d: dict = {}
    if family is not None:
        d["ecu_family"] = family
    if manufacturer is not None:
        d["manufacturer"] = manufacturer
    return d


def _cs(me7=None, ms43=None, denso=None, ironfelix=None) -> dict:
    """Checksum summary in the ``_summarize_checksums`` shape."""
    return {
        "schemes": [],
        "me7": me7,
        "denso": denso,
        "ms43": ms43,
        "ironfelix": ironfelix or [],
    }


def _me7_ident() -> dict:
    """A scorable ME7 identity (canonical Bosch SW → +30 baseline)."""
    return {
        "manufacturer": "Bosch",
        "ecu_family": "ME7.1",
        "software_version": "1037504711",
    }


# ---------------------------------------------------------------------------
# identity ↔ checksum rules
# ---------------------------------------------------------------------------


def test_identity_unknown_checksum_fired_is_gap():
    rep = check_coherence(
        _ident(), _cs(ms43={"ok": 3, "total": 3}), _xr()
    )
    cs_check = rep.checks[0]
    assert cs_check.name == "identity_checksum"
    assert cs_check.status == "gap"
    assert "identity family unknown" in cs_check.detail
    assert not rep.conflict


def test_checksum_matches_and_verifies_is_agree():
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(me7={"status": "ok", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "agree"
    assert rep.checks[1].status == "agree"
    assert rep.status == "agree"
    assert not rep.conflict


def test_checksum_matches_but_unverified_is_stale():
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(me7={"status": "stale", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "stale"
    assert rep.status == "stale"
    assert not rep.conflict  # stale is an explanation, never a conflict


def test_ms43_unverified_on_ms43_is_stale():
    rep = check_coherence(
        _ident(family="MS43", manufacturer="Siemens"),
        _cs(ms43={"ok": 2, "total": 3}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "stale"
    assert rep.status == "stale"


def test_checksum_family_conflict_verified_other_family():
    # A *verified* MS43 CRC16 on an ME7-identified file is a hard conflict.
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(ms43={"ok": 3, "total": 3}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "conflict"
    assert rep.conflict is True
    assert rep.status == "conflict"


def test_checksum_other_family_unverified_is_not_conflict():
    # A detector that fired but verified nothing (absent/stale checks on a
    # file of another family) is noise — it must not fabricate a conflict
    # out of a legitimate tuned file.
    rep = check_coherence(
        _ident(family="MS43", manufacturer="Siemens"),
        _cs(ms43={"ok": 2, "total": 3}, denso={"table_offset": 0, "ok": 0, "total": 8}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "stale"  # matching family drives the verdict
    assert not rep.conflict


def test_no_checksum_family_is_gap():
    rep = check_coherence(_ident(family="ME7.1"), _cs(), _xr(arch="c166"))
    assert rep.checks[0].status == "gap"
    assert rep.checks[0].detail == "no checksum detector ran"


def test_checksums_none_is_gap():
    rep = check_coherence(_ident(family="ME7.1"), None, _xr(arch="c166"))
    assert rep.checks[0].status == "gap"


def test_denso_family_matches_via_manufacturer():
    # Denso identity families are the SHxxxx CPU families — the Denso
    # checksum family matches via the manufacturer (and the SH prefix).
    rep = check_coherence(
        _ident(family="SH7058", manufacturer="Denso"),
        _cs(denso={"table_offset": 0x100, "ok": 8, "total": 8}),
        _xr(arch="sh"),
    )
    assert rep.checks[0].status == "agree"
    assert rep.checks[1].status == "agree"
    assert rep.status == "agree"


def test_ironfelix_me7_profile_agrees():
    rep = check_coherence(
        _ident(family="ME7.6.2", manufacturer="Bosch"),
        _cs(ironfelix=[{"description": "VAG Bosch ME7.XX", "ok": 5, "total": 5}]),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "agree"


def test_ironfelix_unmapped_profile_is_gap_not_conflict():
    # IronFelix profiles with no identity-family mapping (e.g. TCU/Sagem)
    # can neither agree nor conflict.
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(ironfelix=[{"description": "Siemens SMG II TCU (32 KB data)", "ok": 1, "total": 1}]),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "gap"
    assert not rep.conflict


# ---------------------------------------------------------------------------
# identity ↔ arch (xref) rules
# ---------------------------------------------------------------------------


def test_arch_matches_expected_cpu_is_agree():
    rep = check_coherence(
        _ident(family="EDC17", manufacturer="Bosch"),
        _cs(),
        _xr(arch="tricore"),
    )
    assert rep.checks[1].status == "agree"


def test_arch_skipped_is_gap_with_reason():
    rep = check_coherence(
        _ident(family="EDC17", manufacturer="Bosch"),
        _cs(),
        _xr(status="skipped", arch=None, skip_reason="no_code_regions"),
    )
    check = rep.checks[1]
    assert check.status == "gap"
    assert "no_code_regions" in check.detail


def test_arch_none_is_gap():
    rep = check_coherence(_ident(family="EDC17", manufacturer="Bosch"), _cs(), None)
    assert rep.checks[1].status == "gap"


def test_arch_cascade_on_unmapped_family_is_gap_not_conflict():
    # SIMOS has no arch-table entry; the cascade detected TriCore.  Positive
    # (a gap being filled) but not an identity↔arch agreement.
    rep = check_coherence(
        _ident(family="SIMOS", manufacturer="Siemens"),
        _cs(),
        _xr(arch="tricore"),
    )
    check = rep.checks[1]
    assert check.status == "gap"
    assert "unmapped family" in check.detail
    assert not rep.conflict


# ---------------------------------------------------------------------------
# Overall status ordering: conflict > gap > stale > agree
# ---------------------------------------------------------------------------


def test_status_ordering_conflict_wins():
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(ms43={"ok": 3, "total": 3}),
        _xr(status="skipped", arch=None, skip_reason="no_code_regions"),
    )
    assert rep.status == "conflict"


def test_status_ordering_stale_beats_agree():
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(me7={"status": "stale", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    assert rep.checks[0].status == "stale"
    assert rep.checks[1].status == "agree"
    assert rep.status == "stale"


# ---------------------------------------------------------------------------
# score_identity coherence feed
# ---------------------------------------------------------------------------


def test_score_identity_coherence_agree_bonus():
    base = score_identity(_me7_ident(), filename="stock.bin")
    rep = check_coherence(
        _me7_ident(),
        _cs(me7={"status": "ok", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    boosted = score_identity(_me7_ident(), filename="stock.bin", coherence=rep)
    assert boosted.score == base.score + _AGREE_BONUS
    assert any(
        s.label == "cross-signal agreement (identity/checksum/arch)"
        for s in boosted.signals
    )
    assert boosted.warnings == base.warnings


def test_score_identity_coherence_conflict_penalty():
    rep = check_coherence(
        _me7_ident(),
        _cs(ms43={"ok": 3, "total": 3}),
        _xr(arch="c166"),
    )
    base = score_identity(_me7_ident(), filename="stock.bin")
    penalised = score_identity(_me7_ident(), filename="stock.bin", coherence=rep)
    assert penalised.score == base.score - _CONFLICT_PENALTY
    assert "SIGNAL CONFLICT" in penalised.warnings
    assert any(
        s.label == "conflicting identity/checksum/arch signals"
        for s in penalised.signals
    )


def test_score_identity_coherence_stale_warning_only():
    rep = check_coherence(
        _me7_ident(),
        _cs(me7={"status": "stale", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    base = score_identity(_me7_ident(), filename="stock.bin")
    stale = score_identity(_me7_ident(), filename="stock.bin", coherence=rep)
    assert stale.score == base.score
    assert stale.tier == base.tier
    assert "CHECKSUM STALE (tuned file)" in stale.warnings


def test_score_identity_coherence_none_parity():
    base = score_identity(_me7_ident(), filename="stock.bin")
    with_none = score_identity(_me7_ident(), filename="stock.bin", coherence=None)
    assert with_none.score == base.score
    assert with_none.signals == base.signals
    assert with_none.warnings == base.warnings


def test_score_identity_unknown_family_ignores_coherence():
    rep = check_coherence(
        _ident(),
        _cs(ms43={"ok": 3, "total": 3}),
        _xr(arch="c166"),
    )
    result = score_identity(_ident(), filename="x.bin", coherence=rep)
    assert result.tier == "Unknown"
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_report_to_dict_json_safe():
    rep = check_coherence(
        _ident(family="ME7.1", manufacturer="Bosch"),
        _cs(me7={"status": "ok", "scheme": "me7_main"}),
        _xr(arch="c166"),
    )
    blob = json.dumps(rep.to_dict())
    d = json.loads(blob)
    assert d["status"] == "agree"
    assert d["conflict"] is False
    assert d["verdict"]
    assert len(d["checks"]) == 2
    assert {c["name"] for c in d["checks"]} == {"identity_checksum", "identity_arch"}


# ---------------------------------------------------------------------------
# Corpus-gated (skip when tests/data/ is absent — CI never has it)
# ---------------------------------------------------------------------------

_MS43_DIR = Path("tests/data/ECUs/Siemens/MS43")


def _load_ms43(name: str) -> bytes | None:
    p = _MS43_DIR / name
    return p.read_bytes() if p.is_file() else None


def test_corpus_ms43_base_agree():
    data = _load_ms43("MS43_WBABW510X0PK46741_430069_512KB.bin")
    if data is None:
        pytest.skip("MS43 corpus absent")
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(data, "MS43_base.bin")
    assert report.coherence is not None
    # Identity MS43 + MS43 CRC16 verified + C166 arch → agree (at worst a
    # clean gap if a detector is missing — never a fabricated conflict).
    assert report.coherence.status != "conflict"
    cs_check = next(
        c for c in report.coherence.checks if c.name == "identity_checksum"
    )
    assert cs_check.status == "agree"


def test_corpus_ms43_mod_checksum_stale():
    data = _load_ms43("MS43_430069_mod_cruise.bin")
    if data is None:
        pytest.skip("MS43 corpus absent")
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(data, "MS43_mod.bin")
    assert report.coherence is not None
    cs_check = next(
        c for c in report.coherence.checks if c.name == "identity_checksum"
    )
    # Calibration edits without recalculation → stale, never a conflict.
    assert cs_check.status == "stale"
    assert not report.coherence.conflict


def test_corpus_unknown_family_no_crash():
    simos_dir = Path("tests/data/ECUs/Siemens/SIMOS")
    if not simos_dir.is_dir():
        pytest.skip("SIMOS corpus absent")
    files = sorted(p for p in simos_dir.iterdir() if p.is_file())
    if not files:
        pytest.skip("SIMOS corpus empty")
    from openremap.core.services.analyze import analyze_binary

    report = analyze_binary(files[0].read_bytes(), files[0].name)
    assert report.coherence is not None
    # Gap (no detector / identity unknown) or cascade-detected arch —
    # never a crash, never a conflict fabricated from nothing.
    assert report.coherence.status in ("agree", "stale", "gap")
