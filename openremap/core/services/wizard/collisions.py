"""
Collision measurement: which known families would a candidate marker hit?

This is the reason the wizard exists. Detection is **first match wins** over an
ordered registry (`BUILTIN_EXTRACTORS`), so a marker that also occurs in another
family's file does not merely "look untidy" — it changes which extractor wins,
for binaries that used to be identified correctly.

Three checks, all measured against the fixtures shipped in this package (see
:mod:`~openremap.core.services.wizard.fixtures` for why they are trustworthy as
warnings and worthless as proof):

1. **marker in a known-family file** — the candidate's marker occurs in a
   fixture of another family, so a real file of that family would very likely
   trigger the contributor's `can_handle()` too. Reported per family, with the
   *first* offset, because the position tells the reviewer whether the overlap
   is a shared ident dialect or a coincidence in calibration data.
2. **existing claim on the contributor's dump** — some extractor already returns
   `True` for the dump. Either the family is already handled (nothing to do), or
   the new extractor must be ordered before it and must carry an exclusion
   guard. The wizard reports who, and the generator writes the guard.
3. **what the generated extractor does** — after rendering the skeleton, the
   wizard executes it and runs `can_handle()` over every fixture
   (:mod:`~openremap.core.services.wizard.verify`). Checks 1 and 3 disagree in
   practice: a marker can be absent from every fixture and still let a loose
   *guard-free* skeleton steal a file, which is exactly what the round trip is
   there to catch.
"""

from __future__ import annotations

from dataclasses import dataclass

from openremap.core.manufacturers import get_extractors
from openremap.core.services.wizard.fixtures import KnownFixture, known_fixtures


@dataclass(frozen=True)
class Collision:
    """A known-family fixture that the candidate marker also occurs in."""

    fixture: str
    manufacturer: str
    family: str
    offset: int

    def as_dict(self) -> dict:
        return {
            "fixture": self.fixture,
            "manufacturer": self.manufacturer,
            "family": self.family,
            "offset": self.offset,
        }


@dataclass(frozen=True)
class Claim:
    """An existing extractor that already accepts the contributor's dump."""

    module: str
    name: str
    family: str
    strength: str

    def as_dict(self) -> dict:
        return {
            "extractor": self.module,
            "manufacturer": self.name,
            "family": self.family,
            "detection_strength": self.strength,
        }


def marker_collisions(
    marker: bytes, fixtures: tuple[KnownFixture, ...] | None = None
) -> list[Collision]:
    """Every known fixture containing *marker*, in fixture-name order."""
    out: list[Collision] = []
    for fixture in fixtures if fixtures is not None else known_fixtures():
        offset = fixture.data.find(marker)
        if offset >= 0:
            out.append(
                Collision(
                    fixture=fixture.name,
                    manufacturer=fixture.manufacturer,
                    family=fixture.family_key,
                    offset=offset,
                )
            )
    return out


def existing_claim(data: bytes) -> Claim | None:
    """The extractor that already wins on *data*, if any (registry order)."""
    for extractor in get_extractors():
        if extractor.can_handle(data):
            module = type(extractor).__module__
            strength = getattr(extractor.detection_strength, "value", None) or str(
                extractor.detection_strength
            )
            families = extractor.supported_families or []
            return Claim(
                module=module,
                name=extractor.name,
                family=families[0] if families else "?",
                strength=strength,
            )
    return None


def marker_offsets(data: bytes, marker: bytes, limit: int = 5) -> list[int]:
    """All offsets of *marker* in *data* (bounded) — used for field hints."""
    out: list[int] = []
    start = 0
    while len(out) < limit:
        found = data.find(marker, start)
        if found < 0:
            break
        out.append(found)
        start = found + 1
    return out
