"""
Coherence check — identity / checksum / xref must agree, or explain why.

Three independent systems reach their own conclusion in isolation:
**identity** (manufacturer extractor), **checksums** (the detector
registry), **xrefs** (the CPU decoder + arch table/cascade).  This module
is the thin **rule-based** layer that compares them and produces one
verdict per cross-check:

    agree     all signals point the same way
    stale     a tuned file whose checksum was never recalculated —
              an *explanation*, not a failure
    gap       a detector that never ran, or identity that never declared
              an expectation — also not a failure
    conflict  the only hard red flag — signals actively disagree

Safety posture: ``conflict`` is the only hard flag.  ``stale`` and
``gap`` are explanations.  The report feeds ``score_identity`` as
evidence (agree +10, conflict -15 + a warning) and surfaces as a
consistency line in ``analyze``.

Pure function — inputs are already-computed dicts/reports, no
re-scanning.  Deliberately does **not** import ``identify.confidence``
(that module imports *this* one's type) so there is no import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openremap.core.arch import arch_for_family

if TYPE_CHECKING:
    from openremap.core.arch.refs import XrefReport

# ---------------------------------------------------------------------------
# Tunable score deltas
# ---------------------------------------------------------------------------
# Applied by ``score_identity`` when a coherence report is supplied.  Kept
# here (not in confidence.py) so both sides read the same constants.

_AGREE_BONUS = 10
_CONFLICT_PENALTY = 15

#: Worst-first ordering for the overall report status.
_STATUS_RANK = {"conflict": 4, "gap": 3, "stale": 2, "agree": 1, "n/a": 0}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoherenceCheck:
    """One cross-check's verdict."""

    name: str  # "identity_checksum" | "identity_arch"
    status: str  # "agree" | "stale" | "gap" | "conflict" | "n/a"
    detail: str  # one-line human explanation


@dataclass(frozen=True)
class CoherenceReport:
    """Full coherence verdict for one binary.

    ``status`` is the worst of the individual checks (conflict > gap >
    stale > agree); ``conflict`` is True iff any check is a conflict.
    """

    status: str
    checks: tuple[CoherenceCheck, ...]
    conflict: bool

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def verdict(self) -> str:
        """One-line human summary."""
        if self.status == "agree":
            return "identity ✓ checksum ✓ arch ✓"
        if self.status == "conflict":
            return "identity/checksum/arch signals conflict — see below"
        if self.status == "stale":
            return "checksum stale (tuned file)"
        if self.status == "gap":
            return "coverage gap — some signals never ran"
        return "no coherence data"

    def to_dict(self) -> dict:
        """JSON-safe representation (stdlib-json serialisable)."""
        return {
            "status": self.status,
            "conflict": self.conflict,
            "verdict": self.verdict,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


def _make_report(checks: list[CoherenceCheck]) -> CoherenceReport:
    """Combine checks; overall status is the worst of the individual ones."""
    if not checks:
        return CoherenceReport(status="n/a", checks=(), conflict=False)
    status = max((c.status for c in checks), key=lambda s: _STATUS_RANK.get(s, 0))
    return CoherenceReport(
        status=status,
        checks=tuple(checks),
        conflict=any(c.status == "conflict" for c in checks),
    )


# ---------------------------------------------------------------------------
# Checksum side — detector → family derivation
# ---------------------------------------------------------------------------
# The extended ``_summarize_checksums`` dict carries one key per detector
# (mirroring ``health._check_checksums``).  Each fired detector is
# normalised to ``_Detector(label, tokens, verified)`` where ``tokens``
# are ECU-family prefixes (or exact manufacturer names) it implies.

@dataclass(frozen=True)
class _Detector:
    label: str  # display name, e.g. "ME7", "Denso", "IronFelix <desc>"
    tokens: tuple[str, ...]  # family tokens matched against identity
    verified: bool  # every check in the detector verified (ok == total)


#: Non-IronFelix detector key → family token(s).
#: ``Denso`` tokens cover both the manufacturer name (Denso identity
#: families are the SHxxxx CPU families, not "Denso") and the "SH" prefix.
_DETECTOR_TOKENS: dict[str, tuple[str, ...]] = {
    "me7": ("ME7",),
    "ms43": ("MS43",),
    "denso": ("Denso", "SH"),
}

#: IronFelix profile description → family token(s).  Profiles without an
#: identity-family mapping (e.g. "Sagem Iran Khodro", "Siemens GS20 TCU")
#: have empty tokens: they still count as "detector fired" but can neither
#: agree nor conflict.
_IRONFELIX_TOKENS: dict[str, tuple[str, ...]] = {
    "VAG Bosch ME7.XX": ("ME7",),
    "Bosch M3.x-5.x": ("M3", "M5"),
    "Hyundai Bosch M7.9.7": ("M7.9.7",),
    "Hyundai Bosch M7.9.8": ("M7.9.8",),
    "China Bosch M7.9.7": ("M7.9.7",),
    "Citroen Bosch ME7.4.5": ("ME7",),
}


def _family_matches(token: str, family: str | None, manufacturer: str | None) -> bool:
    """Does *token* match the identity side?

    Tokens are ECU-family prefixes (case-insensitive ``startswith`` — the
    same convention as the arch table) OR exact manufacturer names
    (``Denso``: Denso identity families are the SHxxxx CPU families, so
    the checksum family is matched against the manufacturer instead).
    """
    if not family:
        return False
    up = token.upper()
    if family.upper().startswith(up):
        return True
    return bool(manufacturer) and manufacturer.upper() == up


def _fired_detectors(checksums: dict) -> list[_Detector]:
    """Normalise every fired detector in a checksum summary."""
    out: list[_Detector] = []
    for key, tokens in _DETECTOR_TOKENS.items():
        entry = checksums.get(key)
        if entry is None:
            continue
        if key == "me7":
            label, verified = "ME7", entry.get("status") == "ok"
        elif key == "ms43":
            label, verified = "MS43", bool(entry.get("total")) and entry.get("ok") == entry.get("total")
        else:  # denso
            label, verified = "Denso", bool(entry.get("total")) and entry.get("ok") == entry.get("total")
        out.append(_Detector(label=label, tokens=tokens, verified=bool(verified)))
    for prof in checksums.get("ironfelix") or ():
        desc = prof.get("description", "")
        out.append(
            _Detector(
                label=f"IronFelix {desc}",
                tokens=_IRONFELIX_TOKENS.get(desc, ()),
                verified=bool(prof.get("total"))
                and prof.get("ok") == prof.get("total"),
            )
        )
    return out


def _checksum_check(identity: dict, checksums: dict | None) -> CoherenceCheck:
    """identity family vs checksum-detected family (plan §3.1)."""
    family = identity.get("ecu_family")
    manufacturer = identity.get("manufacturer")
    if not checksums:
        return CoherenceCheck(
            "identity_checksum", "gap", "no checksum summary available"
        )
    fired = _fired_detectors(checksums)
    if not fired:
        return CoherenceCheck("identity_checksum", "gap", "no checksum detector ran")
    if not family:
        names = ", ".join(d.label for d in fired)
        return CoherenceCheck(
            "identity_checksum",
            "gap",
            f"identity family unknown — checksum ({names}) can't be cross-checked",
        )
    agreeing = [
        d for d in fired
        if any(_family_matches(t, family, manufacturer) for t in d.tokens)
    ]
    # A *verified* checksum from a different family is a hard conflict.
    # Detectors that fired but found nothing valid (absent/stale checks on
    # a file of another family) are noise — they must not fabricate a
    # conflict out of a legitimate tuned/stale file.
    conflicting = [
        d for d in fired
        if d.verified and d.tokens
        and not any(_family_matches(t, family, manufacturer) for t in d.tokens)
    ]
    if conflicting:
        names = ", ".join(d.label for d in conflicting)
        return CoherenceCheck(
            "identity_checksum",
            "conflict",
            f"checksum detector(s) {names} disagree with identity family {family}",
        )
    if not agreeing:
        labels = ", ".join(d.label for d in fired)
        return CoherenceCheck(
            "identity_checksum",
            "gap",
            f"checksum detector(s) {labels} fired but map to no identity family",
        )
    if all(d.verified for d in agreeing):
        fams = ", ".join(sorted({d.label for d in agreeing}))
        return CoherenceCheck(
            "identity_checksum",
            "agree",
            f"checksum family {fams} agrees with identity {family}",
        )
    return CoherenceCheck(
        "identity_checksum",
        "stale",
        f"checksum family matches identity {family} but the value does not verify "
        "(tuned file — checksum not recalculated)",
    )


def _arch_check(identity: dict, xrefs: "XrefReport | None") -> CoherenceCheck:
    """identity family vs xref-detected arch (plan §3.2)."""
    family = identity.get("ecu_family")
    manufacturer = identity.get("manufacturer")
    if not family:
        return CoherenceCheck(
            "identity_arch", "gap", "identity family unknown — arch can't be cross-checked"
        )
    if xrefs is None:
        return CoherenceCheck("identity_arch", "gap", "no xref pass ran")
    if xrefs.status != "ok":
        reason = xrefs.skip_reason or xrefs.status
        return CoherenceCheck(
            "identity_arch", "gap", f"xref pass skipped ({reason})"
        )
    expected = arch_for_family(manufacturer, family)
    if expected is None:
        # The CPU-detection cascade found an arch for a family with no
        # verified table entry — positive (a gap being filled), but not an
        # identity↔arch *agreement* because identity never declared one.
        return CoherenceCheck(
            "identity_arch",
            "gap",
            f"arch detected ({xrefs.arch}) for unmapped family {family} — "
            "detection cascade filling a gap",
        )
    expected_arch = expected[0]
    if xrefs.arch == expected_arch:
        return CoherenceCheck(
            "identity_arch",
            "agree",
            f"xref arch {xrefs.arch} matches the expected CPU for {family}",
        )
    return CoherenceCheck(
        "identity_arch",
        "conflict",
        f"xref arch {xrefs.arch} conflicts with expected CPU {expected_arch} for {family}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_coherence(
    identity: dict,
    checksums: dict | None,
    xrefs: "XrefReport | None",
) -> CoherenceReport:
    """Compare identity vs checksum-detected family and vs xref arch.

    Args:
        identity:  Dict produced by ``identify_ecu()`` (``ecu_family``,
                   ``manufacturer``).
        checksums: The ``_summarize_checksums`` dict (or ``None`` when the
                   checksum pass never ran — ``--fast``).
        xrefs:     The ``collect_xrefs``/``detect_arch`` report (or ``None``
                   when the xref pass never ran).

    Returns:
        :class:`CoherenceReport` with one :class:`CoherenceCheck` per
        cross-check and an overall worst-case ``status``.
    """
    return _make_report(
        [_checksum_check(identity, checksums), _arch_check(identity, xrefs)]
    )
