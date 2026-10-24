"""
Candidate family signatures, measured from the contributor's own dumps.

The question this module answers is the one a contributor cannot answer by
reading their own file: *which byte strings are stable across my family and
absent from every family the engine already recognises?*

How a candidate is found: printable ASCII runs are harvested from the header
region of every provided dump (early, so ``can_handle()`` stays cheap), the
**intersection** is taken — a marker that is missing from one of the
contributor's own dumps is not a marker for the family — and each survivor is
scored. Nothing is trusted because it looks meaningful: a candidate is only
worth proposing if it is long enough to be unlikely, does not sit inside a
generic-looking run, and does not already occur in a known-family fixture
(:mod:`~openremap.core.services.wizard.collisions` decides that part).

The output is a **ranked list to choose from**, never a single answer. The human
knows their ECU; the tool only removes the guesswork about the bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Runs shorter than this occur by chance far too often to identify anything.
MIN_MARKER_LEN = 6

# Harvest window: real ident blocks sit in the first kilobytes of a dump, and a
# marker deep in calibration data would make `can_handle()` scan the whole file.
HEADER_WINDOW = 0x20000

# A run made of a single repeated character ("\xff\xff…", "00000") is fill, not
# an identifier, and a long digit run is usually a checksum or a table value.
_REPEATED = re.compile(rb"^(.)\1+$")
_GENERIC_TOKENS = frozenset(
    {
        b"BOSCH",
        b"BOSCH0100",
        b"SIEMENS",
        b"CONTINENTAL",
        b"DELPHI",
        b"DENSO",
        b"MOTRONIC",
        b"MARELLI",
        b"HITACHI",
        b"Copyright",
        b"unknown.bin",
    }
)

_ASCII_RUN = re.compile(rb"[\x20-\x7e]{%d,}" % MIN_MARKER_LEN)

# A long printable stretch usually holds several fields separated by padding, so
# each run is also offered split into tokens and trimmed of edge punctuation —
# a marker with trailing spaces would be planted verbatim into the fixture and
# into `can_handle()`, which is never what the contributor means.
_EDGE = b" \t.,;:!?-_+*/\\|"
_SPLIT = re.compile(rb"\s{2,}|\t")


def _variants(run: bytes) -> list[bytes]:
    """Trimmed run, its padding-separated pieces, and their edge-trimmed forms."""
    out: list[bytes] = []

    def add(piece: bytes) -> None:
        trimmed = piece.strip(_EDGE)
        if trimmed and trimmed not in out:
            out.append(trimmed)

    add(run)
    for piece in _SPLIT.split(run):
        add(piece)
        # "SIEMENS SID206 1.0" → also offer the bare tokens: a single space is
        # meaningful text, but the token alone is the stabler signature.
        for word in piece.split(b" "):
            if len(word) >= MIN_MARKER_LEN:
                add(word)
    return [piece for piece in out if len(piece) >= MIN_MARKER_LEN]


def _harvest(data: bytes, window: int = HEADER_WINDOW) -> list[tuple[int, bytes]]:
    """Candidate markers with their offsets, from the header region."""
    head = data[:window]
    out: list[tuple[int, bytes]] = []
    for match in _ASCII_RUN.finditer(head):
        for piece in _variants(match.group(0)):
            out.append((match.start(), piece))
    return out


@dataclass
class Candidate:
    """One proposed family signature, with the evidence behind its score."""

    marker: bytes
    score: int
    present_in: int                      # how many of the provided dumps
    offsets: dict[str, int] = field(default_factory=dict)  # dump name → first offset
    reasons: list[str] = field(default_factory=list)
    generic_reason: str | None = None    # why this marker cannot identify anything

    @property
    def text(self) -> str:
        """ASCII view for a report; non-printables shown as dots."""
        return "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in self.marker)

    def as_dict(self) -> dict:
        return {
            "marker": self.marker.decode("latin-1"),
            "marker_hex": self.marker.hex(),
            "marker_len": len(self.marker),
            "score": self.score,
            "offsets": dict(self.offsets),
            "reasons": list(self.reasons),
            "generic": self.generic_reason,
        }


#: Shortest / longest motif that a data run can be built from.
_MOTIF_MIN, _MOTIF_MAX = 2, 8


def repeated_motif(marker: bytes) -> str | None:
    """A description when *marker* is mostly one short motif, else ``None``.

    Printable calibration data is not an identifier. Measured on a real family
    (Denso SH7058): two dumps of the same model year produced the candidate
    ``'gCaCA$3:f3F$&j4j3j#WD$3TD$3TD$3TD…'`` — 152 printable bytes, of which 120
    are ``$3TD`` repeated. It was ranked first, recommended, and covered 22 of the
    family's 35 dumps; two dumps from different years produced
    ``'Copyright 1999 Hitachi,Ltd.Ho7058 Operating System, Version '`` and covered
    35/35. The difference is not length or letters — it is that one of them is a
    repeating pattern and the other is a sentence.
    """
    body = marker.strip()
    if len(body) < 16:
        return None
    best: tuple[int, int, bytes] = (0, 0, b"")
    for period in range(_MOTIF_MIN, _MOTIF_MAX + 1):
        # Every alignment, not just offset 0: in the measured case the repeating
        # part starts after 20 bytes of real text ("gCaCA$3:f3F$&j4j3j#WD").
        for offset in range(period):
            chunks = [
                body[start : start + period]
                for start in range(offset, len(body) - period + 1, period)
            ]
            index = 0
            while index < len(chunks):
                end = index
                while end < len(chunks) and chunks[end] == chunks[index]:
                    end += 1
                if end - index > best[0]:
                    best = (end - index, (end - index) * period, chunks[index])
                index = max(end, index + 1)
    repeats, covered, unit = best
    if repeats >= 6 and covered >= len(body) * 0.6:
        return (
            f"one motif ({unit.decode('latin-1')!r}) repeated {repeats} times — "
            "printable data, not an identifier"
        )
    return None


def _generic_reason(marker: bytes) -> str | None:
    """Why this run cannot be a family signature, or None if it might be.

    These runs are *kept* rather than dropped: a contributor whose only stable
    run is ``MOTRONIC`` needs to be told that it is shared vocabulary, not told
    nothing at all. Only runs that are structurally meaningless — fill bytes and
    bare values — are filtered out, and the count of what was filtered is
    reported so an empty result can always be explained.
    """
    stripped = marker.strip()
    if len(stripped) < MIN_MARKER_LEN:
        return "too short to be distinctive"
    if _REPEATED.match(stripped):
        return "a single repeated character — fill, not an identifier"
    if stripped in _GENERIC_TOKENS:
        return "a manufacturer/toolchain label shared by many families"
    if stripped.isdigit():
        return "digits only — a value, and the engine's SW/HW patterns cover those"
    motif = repeated_motif(marker)
    if motif:
        return motif
    return None


def _structural(marker: bytes) -> bool:
    """True when the run is meaningless *as a string* (dropped, but counted)."""
    stripped = marker.strip()
    return (
        len(stripped) < MIN_MARKER_LEN
        or bool(_REPEATED.match(stripped))
        or stripped.isdigit()
    )


def _score(marker: bytes, dump_count: int, stability: int) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    length = len(marker)
    score += min(length, 24) * 2
    reasons.append(f"{length} printable bytes")

    if stability == dump_count and dump_count > 1:
        score += 40
        reasons.append(f"present in all {dump_count} dumps")
    elif dump_count == 1:
        reasons.append("only one dump provided — stability unverified")

    letters = sum(1 for b in marker if chr(b).isalpha())
    digits = sum(1 for b in marker if chr(b).isdigit())
    if letters >= 3:
        score += 10
        reasons.append(f"{letters} letters (name-like)")
    if digits and letters:
        score += 6
        reasons.append("mixed letters and digits (typical OEM token)")
    if b" " in marker:
        score -= 8
        reasons.append("contains spaces — likely prose or padding")
    return score, reasons


def find_candidates(
    dumps: list[tuple[str, bytes]],
    *,
    window: int = HEADER_WINDOW,
    min_len: int = MIN_MARKER_LEN,
    limit: int = 12,
    stats: dict | None = None,
) -> list[Candidate]:
    """Rank markers shared by every provided dump.

    *dumps* is ``[(name, bytes), …]``; one dump is allowed (the wizard says so in
    the report rather than pretending stability was checked).

    *stats*, when given, is filled with the counts behind the result — runs seen,
    runs dropped as structurally meaningless, and runs that were not shared by
    every dump. An empty candidate list must never be a dead end, so the report
    prints those numbers.
    """
    if not dumps:
        return []

    harvested = {name: _harvest(data, window) for name, data in dumps}
    tally: dict[bytes, dict[str, int]] = {}
    runs_seen = 0
    for name, runs in harvested.items():
        for offset, run in runs:
            if len(run) < min_len:
                continue
            runs_seen += 1
            tally.setdefault(run, {})[name] = offset

    dump_names = [name for name, _ in dumps]
    candidates: list[Candidate] = []
    dropped_structural = 0
    dropped_unstable = 0
    for marker, offsets in tally.items():
        if len(offsets) != len(dump_names):
            dropped_unstable += 1
            continue  # not stable across the contributor's own dumps
        if _structural(marker):
            dropped_structural += 1
            continue
        score, reasons = _score(marker, len(dump_names), len(offsets))
        generic = _generic_reason(marker)
        if generic:
            score -= 60
            reasons.append(f"generic: {generic}")
        candidates.append(
            Candidate(
                marker=marker,
                score=score,
                present_in=len(offsets),
                offsets={name: offsets[name] for name in dump_names},
                reasons=reasons,
                generic_reason=generic,
            )
        )

    # Non-generic first, then score, then longest — and stable for reproducible
    # reports. A generic marker is never hidden, only ranked below the useful
    # ones, because "your only stable run is MOTRONIC" is an answer.
    candidates.sort(
        key=lambda c: (
            c.generic_reason is not None,
            -c.score,
            -len(c.marker),
            c.marker,
        )
    )
    if stats is not None:
        stats.update(
            {
                "runs_seen": runs_seen,
                "distinct_runs": len(tally),
                "dropped_structural": dropped_structural,
                "dropped_unstable": dropped_unstable,
                "dumps": len(dump_names),
                "window": window,
            }
        )
    return candidates[:limit]


def sub_markers(candidate: Candidate, *, min_len: int = MIN_MARKER_LEN) -> list[bytes]:
    """Shorter variants of a marker, for when the full run is too generic.

    A contributor who picks a 24-byte run that collides with an existing family
    often wants the *head* of it; offering the windows saves a round trip.
    """
    marker = candidate.marker
    out: list[bytes] = []
    width = max(min_len, len(marker) // 2)
    for start in range(0, max(1, len(marker) - min_len + 1)):
        piece = marker[start : start + width]
        if len(piece) >= min_len and piece not in out:
            out.append(piece)
    return out[:6]
