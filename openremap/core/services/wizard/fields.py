"""
Candidate identity fields, read out of the contributor's dumps.

A new extractor needs more than a `can_handle()`: `extract()` must fill
`software_version`, `hardware_number`, `oem_part_number`, `calibration_id` and a
`match_key`, or the recipe layer has nothing to key on. This module proposes
candidates from the dump and — more usefully — **sorts them by the distinction
that decides where they belong**:

* a value that is **identical in every dump the contributor provided** is a
  family constant: it belongs in a fixed field, because it does not identify the
  individual file. It is *not* thereby a signature — two dumps of the same
  calibration cannot distinguish a family token from that calibration's version
  string, which is the trap this module refuses to talk a contributor into;
* a value that **differs between dumps of the same family** is a per-file
  field: that is what a `match_key` needs, because a key that is the same for
  every member of the family cannot tell two calibrations apart.

The field vocabulary is deliberately small and generic — the per-manufacturer
patterns live inside the extractors, and re-exporting 39 of them here would
duplicate knowledge that already has one home. What is offered: the shared VAG
part-number scan, Bosch-style 10-digit hardware/software runs, 12-digit
calibration runs, and slash-delimited descriptors (the Bosch/Marelli ident
dialect).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from openremap.core.manufacturers.bosch._vag import find_vag_part_number

# Ordered: the first pattern that explains a run wins, so the more specific
# shapes are tried before the generic ones.
PATTERNS: tuple[tuple[str, re.Pattern[bytes], str], ...] = (
    ("bosch_hw", re.compile(rb"0261\d{6}"), "Bosch hardware part number (0261……)"),
    # The trailing group is not decoration: a real Bosch software version is
    # ``1037541778126241V0`` — digits *and* a revision suffix. Capping the digit
    # run at 10 (the earlier shape) truncated it to ``10375417781262``, which is
    # a version no binary carries and a match_key that can never match.
    (
        "bosch_sw",
        re.compile(rb"1037\d{6,}[A-Z0-9]{0,4}"),
        "Bosch software version (1037……)",
    ),
    (
        "calibration_version",
        re.compile(rb"(?<![A-Z0-9])CV\d{4,8}(?!\d)"),
        "calibration version token (CV……)",
    ),
    ("sw_12digit", re.compile(rb"(?<![\dA-Z])\d{12}(?![\dA-Z])"), "12-digit run"),
    (
        "descriptor",
        re.compile(rb"\d{1,4}/\d{1,4}/[A-Z][A-Z0-9.\-]{1,12}/[\w/_\- .]{6,}"),
        "slash-delimited ident descriptor (…/…/<family>/…)",
    ),
)

_DESCRIPTOR_FAMILY_FIELD = 2  # field 2 of "rev/sub/family/…" is the family


@dataclass
class FieldCandidate:
    """A proposed identity field and what its values do across the dumps."""

    kind: str
    description: str
    values: dict[str, str]                 # dump name → value
    offsets: dict[str, int] = dc_field(default_factory=dict)
    stable: bool = False

    @property
    def value(self) -> str | None:
        """The single value when the field is stable, else None."""
        distinct = set(self.values.values())
        return next(iter(distinct)) if len(distinct) == 1 else None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "description": self.description,
            "stable": self.stable,
            "value": self.value,
            "values": dict(self.values),
            "offsets": {k: v for k, v in self.offsets.items()},
            # "Stable" means stable *in the dumps the contributor supplied* —
            # which is exactly the evidence that produced a marker made of one
            # calibration's version string. So the advice must not say "put it in
            # can_handle()": a value being constant across two dumps of the same
            # calibration says nothing about the family, and a fixed field is the
            # only use that is safe on this evidence.
            "suggested_use": (
                "constant in your dumps — a fixed field, not proven as a signature"
                if self.stable
                else "varies between your dumps — candidate for match_key"
            ),
        }


def _search(kind: str, pattern: re.Pattern[bytes], dumps: list[tuple[str, bytes]]):
    """First match per dump, plus whether every dump produced one."""
    values: dict[str, str] = {}
    offsets: dict[str, int] = {}
    for name, data in dumps:
        match = pattern.search(data)
        if match:
            values[name] = match.group(0).decode("ascii", errors="ignore")
            offsets[name] = match.start()
    return values, offsets


def find_fields(dumps: list[tuple[str, bytes]]) -> list[FieldCandidate]:
    """Every field candidate the generic vocabulary can find, stable ones first."""
    if not dumps:
        return []

    out: list[FieldCandidate] = []

    vag = {name: find_vag_part_number(data) for name, data in dumps}
    present = {k: v for k, v in vag.items() if v}
    if present:
        offsets = {name: data.find(str(val).encode()) for name, (val, data) in
                   ((n, (vag[n], d)) for n, d in dumps) if val}
        out.append(
            FieldCandidate(
                kind="vag_part_number",
                description="VAG/VW-Audi-Seat-Skoda part number (letter-bearing run)",
                values={k: str(v) for k, v in present.items()},
                offsets=offsets,
                stable=len(set(present.values())) == 1 and len(present) == len(dumps),
            )
        )

    for kind, pattern, description in PATTERNS:
        values, offsets = _search(kind, pattern, dumps)
        if not values:
            continue
        out.append(
            FieldCandidate(
                kind=kind,
                description=description,
                values=values,
                offsets=offsets,
                stable=len(set(values.values())) == 1 and len(values) == len(dumps),
            )
        )

    out.sort(key=lambda c: (not c.stable, c.kind))
    return out


def descriptor_family(value: str) -> str | None:
    """The family field of a slash descriptor, e.g. ``45/1/ME7910/9/…`` → ME7910."""
    parts = value.split("/")
    if len(parts) > _DESCRIPTOR_FAMILY_FIELD:
        candidate = parts[_DESCRIPTOR_FAMILY_FIELD].strip()
        if candidate:
            return candidate
    return None


def suggest_identifiers(dumps: list[tuple[str, bytes]]) -> dict:
    """Family-name suggestions taken from what the dumps call themselves.

    A contributor usually knows the family from the ECU label, but the dump is
    the tie-breaker: the descriptor field and the longest non-generic printable
    runs are what a reviewer would expect the new family key to look like.
    """
    from openremap.core.services.wizard.signatures import find_candidates

    families: list[str] = []
    for _name, data in dumps:
        for kind, pattern, _desc in PATTERNS:
            if kind != "descriptor":
                continue
            # The whole file, not the header: the authoritative descriptor of a
            # 4 MB Bosch dump sits past 0x40000 (measured: 0x40E14), so a
            # 0x20000 window reported "no descriptor" for a dump that has one.
            # Bounded by the early exit rather than by the window.
            for match in pattern.finditer(data):
                found = descriptor_family(match.group(0).decode("ascii", "ignore"))
                if found and found not in families:
                    families.append(found)
                    if len(families) >= 8:
                        break
            if len(families) >= 8:
                break

    return {
        "from_descriptors": families[:8],
        "longest_runs": [c.text for c in find_candidates(dumps, limit=5)],
    }
