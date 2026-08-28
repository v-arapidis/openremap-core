"""
CPU-detection cascade — trial-decode fall-through for unknown families.

When ``arch_for_family(manufacturer, family)`` returns ``None`` (an
unknown/unmapped family), the call sites do **not** skip the xref pass.
Instead they fall through to :func:`detect_arch`, which trial-runs each
candidate decoder and keeps the first whose references plausibly land
inside the map-table data spans.

**Safety contract (presence-only):** the signal is presence-only — a wrong
arch guess yields references that do not hit the table spans, never a false
bonus.  A candidate is accepted only when ALL of the gates in
:func:`_accepts` clear (decode succeeded, enough valid instructions, enough
references inside the data spans).  Do NOT tighten any gate into a penalty
(``_MIN_SPAN_HITS`` only ever rises; it is never subtracted).

Risk note (handoff plan §7 + a discovery): a wrong arch whose garbage
references happen to hit spans could in principle false-positive — bounded
by the presence-only contract and mitigated by raising ``_MIN_SPAN_HITS``
or reordering ``_CASCADE_CANDIDATES``, never by penalising absence.
Second discovery: capstone's SuperH **SH-2A** decoder has a known
out-of-bounds read on crafted bytecode (GHSA-gf2c-xwcp-hvf4 /
CVE-2026-55894; e.g. ``33 51 e5 31``, found in a real 32 KB Bosch M1.3
bin) — a C-level crash that Python cannot catch.  Because the cascade
decodes *unknown* binaries by design, every trial runs in a forked child
(:func:`_trial_collect`) so a decoder crash (or garbage decode) rejects
that candidate instead of killing the process.

This module is deliberately separate from ``arch/__init__.py``: the package
``__init__`` is imported by ``refs.py`` (``from openremap.core.arch import
c166``), so putting ``detect_arch`` in ``__init__`` would create an
``__init__`` → ``refs`` → ``arch.c166`` cycle.  ``detect.py`` is imported
only by the call sites, after the package is fully initialised.
"""

from __future__ import annotations

import faulthandler
import os
import pickle

from capstone import (  # type: ignore[import-untyped]
    CS_ARCH_SH,
    CS_ARCH_TRICORE,
    CS_MODE_SH2,
    CS_MODE_SH2A,
)

from openremap.core.arch.refs import XrefReport, collect_xrefs

# ---------------------------------------------------------------------------
# Candidate cascade
# ---------------------------------------------------------------------------

#: Ordered candidate decoders — first-match-wins.  Tuple shape ==
#: ``arch_for_family`` output: (arch_key, capstone_arch, base_mode,
#: accepts_endian_flag).  c166 first (Rust decoder, fastest, the most
#: common "could serve it" CPU for unknown families), then TriCore, then
#: SuperH (SH-2, then SH-2A).  x86 is deliberately EXCLUDED — it is a
#: test-only generic fallback with absolute addressing that false-positives
#: on arbitrary binaries.
_CASCADE_CANDIDATES: list[tuple[str, int, int, bool]] = [
    ("c166", 0, 0, False),
    ("tricore", CS_ARCH_TRICORE, 0, False),
    ("sh", CS_ARCH_SH, CS_MODE_SH2, True),
    ("sh", CS_ARCH_SH, CS_MODE_SH2A, True),
]

#: Minimum valid instructions before a trial decode counts as plausible
#: (enough real decode, not garbage).
_MIN_INSNS = 50

#: Minimum references landing inside the data spans before an arch is
#: accepted.  Aligns with ``refs._MIN_BASE_HITS`` (3); c166's own window
#: gate is stricter (8) and runs inside ``collect_xrefs`` already.
_MIN_SPAN_HITS = 3


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _skip(reason: str) -> XrefReport:
    """A clean skip report — mirrors ``refs._skip`` (replicated, not
    imported, to avoid reaching into a private of a sibling module)."""
    return XrefReport(
        status="skipped",
        skip_reason=reason,
        arch=None,
        endian=None,
        base_address=0,
        code_bytes_scanned=0,
        insn_count=0,
        referenced=frozenset(),
        refs={},
    )


def _accepts(xr: XrefReport, spans: list[tuple[int, int]]) -> bool:
    """Gate a trial decode: ALL of status ok, enough instructions, enough
    references inside the data spans."""
    if xr.status != "ok":
        return False
    if xr.insn_count < _MIN_INSNS:
        return False
    hits = sum(1 for off in xr.referenced if any(s <= off < e for s, e in spans))
    return hits >= _MIN_SPAN_HITS


# ---------------------------------------------------------------------------
# Crash-isolated trials
# ---------------------------------------------------------------------------


def _trial_collect(
    data: bytes,
    regions: list[tuple[int, int]],
    arch_info: tuple[str, int, int, bool],
    endian: str | None,
    spans: list[tuple[int, int]],
) -> XrefReport | None:
    """Run one ``collect_xrefs`` trial; ``None`` means the decode crashed.

    A capstone C-level segfault (e.g. the SH-2A decoder on garbage bytes)
    is not catchable from Python, and the cascade decodes *unknown* binaries
    by design — so the decode runs in a forked child and the report is
    pickled back.  A child that dies (segfault/abort) yields ``None`` and
    the candidate is simply rejected, exactly like a decode that produced
    no span hits.  Non-POSIX platforms (no ``os.fork``) fall back to an
    in-process decode — a decoder crash there would kill the process, which
    is accepted for the unsupported platform.
    """
    if not hasattr(os, "fork"):
        return collect_xrefs(data, regions, arch_info, endian, spans=spans)
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
    except OSError:  # pragma: no cover - fork under memory pressure
        os.close(read_fd)
        os.close(write_fd)
        return collect_xrefs(data, regions, arch_info, endian, spans=spans)
    if pid == 0:  # child — decode + report, then exit without touching
        # parent state (no atexit / lock cleanup).  stdio is silenced so a
        # decoder crash's "Fatal Python error" line cannot pollute output.
        os.close(read_fd)
        try:
            # A crashing decoder must not leave a fatal-error traceback in
            # the parent's output: silence stdio AND pytest's faulthandler
            # (which holds its own captured fd).
            faulthandler.disable()
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            os.close(devnull)
        except OSError:  # pragma: no cover - devnull always openable
            pass
        try:
            xr = collect_xrefs(data, regions, arch_info, endian, spans=spans)
            with os.fdopen(write_fd, "wb") as f:
                pickle.dump(xr, f, protocol=pickle.HIGHEST_PROTOCOL)
            os._exit(0)
        except BaseException:  # noqa: BLE001 - any decode failure → reject
            os._exit(2)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as f:
        try:
            xr = pickle.load(f)
        except Exception:  # EOF etc. when the child died mid-write
            xr = None
    _pid, status = os.waitpid(pid, 0)
    if status != 0:  # signalled (segfault) or non-zero exit
        return None
    return xr


# ---------------------------------------------------------------------------
# Cascade pass
# ---------------------------------------------------------------------------


def detect_arch(
    data: bytes,
    code_regions: list[tuple[int, int]],
    endian: str | None,
    spans: list[tuple[int, int]],
    *,
    candidates: list[tuple[str, int, int, bool]] | None = None,
) -> XrefReport:
    """Trial-decode candidates; return the winning XrefReport or a skip report.

    Tries each candidate decoder via :func:`collect_xrefs` and returns the
    FIRST whose report clears :func:`_accepts` (no double-decode at the
    call site — the winning report carries the decoded references).  When
    no candidate clears the gates, returns ``XrefReport(status="skipped",
    skip_reason="no_arch_detected")``.

    ``candidates`` is a private test hook — production callers leave it
    None (the module-level :data:`_CASCADE_CANDIDATES` order applies).
    """
    if not code_regions:
        return _skip("no_code_regions")
    if not spans:
        return _skip("no_arch_detected")  # nothing to gate on → conservative
    cands = _CASCADE_CANDIDATES if candidates is None else candidates
    for arch_info in cands:
        xr = _trial_collect(data, code_regions, arch_info, endian, spans)
        if xr is not None and _accepts(xr, spans):
            return xr
    return _skip("no_arch_detected")
