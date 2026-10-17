"""
The wizard's measurement pass: everything a contributor needs to *decide*,
before a single line is generated.

Order matters, and it is the order of the questions a reviewer will ask:

1. **Does the engine already handle this dump?** If an extractor claims it, the
   contribution is either unnecessary or is a change to that extractor — the
   report says which, and stops short of proposing a new family.
2. **What could be the family signature?** Ranked markers shared by every dump
   the contributor provided (:mod:`~openremap.core.services.wizard.signatures`).
3. **Does any marker collide with a family we already recognise?** Per marker,
   the fixtures it occurs in, with offsets
   (:mod:`~openremap.core.services.wizard.collisions`). A marker with no
   collision is the only kind worth proposing, so the report marks it.
4. **What are the identity fields?** Sorted into family constants (identical in
   every dump) and per-file fields (what a `match_key` needs)
   (:mod:`~openremap.core.services.wizard.fields`).

Everything in the returned dict is JSON-safe: the CLI formats it, the API
returns it, and the report file is written from it verbatim.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as dc_field

from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.wizard.collisions import (
    Claim,
    existing_claim,
    marker_collisions,
)
from openremap.core.services.wizard.fixtures import (
    KnownFixture,
    fixtures_available,
    known_fixtures,
)
from openremap.core.services.wizard.fields import find_fields, suggest_identifiers
from openremap.core.services.wizard.signatures import Candidate, find_candidates

# A marker that occurs in this many distinct known families is not a signature:
# it is shared vocabulary (a toolchain label, a common ident dialect).
MAX_COLLIDING_FAMILIES = 3


@dataclass
class DumpView:
    """What the engine currently says about one of the contributor's dumps."""

    name: str
    size: int
    sha256: str
    claimed_by: Claim | None
    identity: dict = dc_field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "claimed_by": self.claimed_by.as_dict() if self.claimed_by else None,
            "identity": self.identity,
        }


@dataclass
class CandidateView:
    """A candidate marker plus its collision verdict."""

    candidate: Candidate
    collisions: list[dict]

    @property
    def families(self) -> list[str]:
        return sorted({c["family"] for c in self.collisions})

    @property
    def clean(self) -> bool:
        """True only when the marker occurs in **no** known-family fixture.

        Deliberately strict: "occurs in lots of families" is not a milder form of
        clean, it is the worst case — the marker is shared vocabulary and
        identifies nothing. An early version of this property returned True for
        that case, which made `MOTRONIC` (7 fixtures, 5 families) print as clean.
        """
        return not self.collisions

    @property
    def verdict(self) -> str:
        families = self.families
        if not families:
            return "no collision — occurs in none of the known-family fixtures"
        if len(families) > MAX_COLLIDING_FAMILIES:
            return (
                f"shared vocabulary — occurs in {len(families)} known families "
                f"({', '.join(families[:4])}…), so it identifies nothing"
            )
        return (
            "collides with "
            + ", ".join(families)
            + " — a real file of that family would very likely trigger your "
            "can_handle() too; add an exclusion guard or choose another marker"
        )

    def as_dict(self) -> dict:
        payload = self.candidate.as_dict()
        payload.update(
            {
                "collisions": self.collisions,
                "collision_families": self.families,
                "clean": self.clean,
                "verdict": self.verdict,
            }
        )
        return payload


def _trim_identity(result: dict) -> dict:
    """The identity fields worth printing — no hashes, no raw string dumps."""
    keys = (
        "manufacturer",
        "ecu_family",
        "ecu_variant",
        "software_version",
        "hardware_number",
        "oem_part_number",
        "calibration_id",
        "match_key",
        "detection_strength",
        "detection_evidence",
    )
    out: dict = {}
    for key in keys:
        value = result.get(key)
        if hasattr(value, "value"):  # DetectionStrength enum
            value = value.value
        out[key] = value
    return out


def analyze(
    dumps: list[tuple[str, bytes]],
    *,
    fixtures: tuple[KnownFixture, ...] | None = None,
    exclude_families: tuple[str, ...] = (),
    candidate_limit: int = 12,
) -> dict:
    """Measure a contribution. Pure: bytes in, JSON-safe dict out.

    *exclude_families* is the family being contributed. Its own fixtures must not
    count as collisions: once a family is in the corpus, its marker is *supposed*
    to be in its own fixture, and without this a contributor iterating on a
    family they already added would be told "no collision-free marker" by their
    own file.
    """
    if not dumps:
        raise ValueError("analyze: at least one dump is required")

    # Passing the same file twice (or two copies of one dump) is not two dumps:
    # it proves nothing about stability, and with the tally keyed by file name it
    # silently produced "no stable runs" — a confusing answer to a confused
    # question. Say so instead.
    seen: dict[str, str] = {}
    unique: list[tuple[str, bytes]] = []
    duplicates: list[str] = []
    for name, data in dumps:
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            duplicates.append(f"{name} (identical to {seen[digest]})")
            continue
        seen[digest] = name
        unique.append((name, data))
    dumps = unique
    duplicated = bool(duplicates)

    full_pool = fixtures if fixtures is not None else known_fixtures()
    own = tuple(
        fixture
        for fixture in full_pool
        if fixture.family in exclude_families or fixture.family_key in exclude_families
    )
    pool = tuple(fixture for fixture in full_pool if fixture not in own)
    # Without the repository's fixture recipes there is nothing to collide with,
    # and a ranking that has not been checked must never be presented as a
    # recommendation. Measure, say what is missing, refuse to advise.
    # Derived from what there was to check against, never from how the pool
    # arrived: an explicit empty pool means nothing was checked, and claiming
    # otherwise would present an unverified ranking as a checked one.
    collisions_checked = bool(full_pool)

    views: list[DumpView] = []
    for name, data in dumps:
        claim = existing_claim(data)
        views.append(
            DumpView(
                name=name,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                claimed_by=claim,
                identity=_trim_identity(identify_ecu(data, name)),
            )
        )

    marker_stats: dict = {}
    candidates = [
        CandidateView(
            candidate=candidate,
            collisions=(
                [c.as_dict() for c in marker_collisions(candidate.marker, pool)]
                if collisions_checked
                else []
            ),
        )
        for candidate in find_candidates(dumps, limit=candidate_limit, stats=marker_stats)
    ]
    # A clean, non-generic marker is the whole point, so it leads regardless of
    # raw score.
    candidates.sort(
        key=lambda v: (
            v.candidate.generic_reason is not None,
            not v.clean,
            -v.candidate.score,
            -len(v.candidate.marker),
        )
    )

    fields = [f.as_dict() for f in find_fields(dumps)]
    recommended = None
    if (
        collisions_checked
        and candidates
        and candidates[0].clean
        and not candidates[0].candidate.generic_reason
    ):
        recommended = candidates[0].candidate.marker.decode("latin-1")

    return {
        "dumps": [v.as_dict() for v in views],
        "already_handled": all(v.claimed_by is not None for v in views),
        "claimed_by": (
            views[0].claimed_by.as_dict() if views and views[0].claimed_by else None
        ),
        "candidates": [v.as_dict() for v in candidates],
        "recommended": recommended,
        "candidate_stats": marker_stats,
        "excluded_own_fixtures": [f.name for f in own],
        "collisions_checked": collisions_checked,
        "notes": _notes(
            marker_stats,
            candidates,
            views,
            collisions_checked,
            [f.name for f in own],
            duplicates,
        ),
        "fields": fields,
        "suggestions": suggest_identifiers(dumps),
        "fixtures_checked": len(pool),
    }


def _notes(
    stats: dict,
    candidates: list["CandidateView"],
    views: list["DumpView"],
    collisions_checked: bool = True,
    excluded_own: list[str] | None = None,
    duplicates: list[str] | None = None,
) -> list[str]:
    """Plain-language explanation of an unhelpful result.

    A wizard that says "no candidates" and stops is worse than no wizard: the
    contributor cannot tell whether their dump lacks a marker, whether they
    needed a second dump, or whether the tool is broken.
    """
    notes: list[str] = []

    if duplicates:
        notes.append(
            "These dumps are identical and were measured once: "
            + ", ".join(duplicates)
            + ". Two copies of one calibration cannot show that a marker is stable "
            "across calibrations — pass a *different* dump of the same family."
        )

    if excluded_own:
        notes.append(
            "Your own family already has "
            + ", ".join(excluded_own)
            + " in the corpus, excluded from collision checking — a marker that "
            "occurs in your own fixture is the point, not a collision."
        )

    if not collisions_checked:
        notes.append(
            "Collisions were NOT checked: this machine has no copy of the "
            "repository's fixture recipes (_dev/contrib/recipes/). Run the wizard "
            "from a checkout of openremap-core — contributing means opening a "
            "pull request, so you need one anyway. Every marker below is "
            "therefore unranked by safety and nothing is recommended."
        )
        notes.append(
            "Everything else in this report is local computation and stands: who "
            "claims your dump, the runs stable across your dumps, and the "
            "candidate identity fields."
        )

    if len(views) == 1:
        notes.append(
            "Only one dump was provided, so a marker that varies between "
            "calibrations of your family cannot be told apart from a family "
            "constant. Pass a second dump of the same family to check stability."
        )

    if not candidates:
        notes.append(
            "No marker candidates survived in the header region: "
            f"{stats.get('distinct_runs', 0)} distinct printable runs were seen, "
            f"{stats.get('dropped_structural', 0)} were fill/values and "
            f"{stats.get('dropped_unstable', 0)} were not shared by every dump. "
            "If your dumps differ a lot between calibrations, retry with "
            "`--window` larger, or pick the marker by hand from the strings above."
        )
    elif all(c.candidate.generic_reason for c in candidates):
        notes.append(
            "Every stable run is generic — see the per-candidate reasons. A "
            "family signature has to be something the family *owns*."
        )

    clean = [c for c in candidates if c.clean and not c.candidate.generic_reason]
    if collisions_checked and candidates and not clean:
        notes.append(
            "No candidate is collision-free. Either pick a longer/shorter window "
            "of a run, or keep the candidate and add an exclusion guard for the "
            "families it collides with — the generator writes that guard for you."
        )
    return notes
