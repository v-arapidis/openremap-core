"""
openremap.api.commands.families — the curated family reference.

Thin gate over ``core/manufacturers/families``: the table lives in the service
layer (it used to live in the CLI, which meant no other consumer could read it).
This is reference material, not detection: a family is only *claimed* when an
extractor fires.
"""

from __future__ import annotations

from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.manufacturers import EXTRACTORS
from openremap.core.manufacturers.families import FAMILIES, find_family, normalize

__all__ = ["FAMILIES", "families", "find_family"]


def _documented(name: str) -> dict | None:
    """Which written reference entry, if any, describes this declared family.

    Matching goes through the reference's own vocabulary — name, aliases and
    ``sub_families`` — because guessing from strings gets it wrong: "EDC15" is
    not a variant of "EDC1", while "EDC16C31" *is* a variant of "EDC16" and
    "ME7.5" of "ME7 / ME7.x". Only after an exact match on that vocabulary do we
    accept a prefix, and then only when the alias ends on a letter (``dme`` →
    ``DME-3.2``), never on a digit.
    """
    needle = normalize(name)

    def keys(entry: dict) -> list[str]:
        return [
            normalize(str(choice))
            for choice in [entry["name"], *entry["aliases"], *entry.get("sub_families", [])]
        ]

    for entry in FAMILIES:
        if needle in keys(entry):
            return entry

    for entry in FAMILIES:
        # An entry whose name carries a version wildcard ("ME7 / ME7.x", "M3.x",
        # "EDC 3.x") covers the whole series; one that names a number
        # ("EDC1 / EDC2") does not extend to the next number ("EDC15").
        wildcard = "x" in normalize(str(entry["name"]))
        for key in keys(entry):
            if not key or not needle.startswith(key):
                continue
            rest = needle[len(key) :]
            if not rest[0].isdigit() or wildcard:
                return entry
    return None


def _declared_families() -> list[dict]:
    """One row per family an extractor declares — the complete coverage list."""
    rows: list[dict] = []
    for extractor in EXTRACTORS:
        for family in extractor.supported_families or []:
            reference = _documented(str(family))
            rows.append(
                {
                    "manufacturer": extractor.name or "unknown",
                    "family": str(family),
                    "extractor": type(extractor).__name__.replace("Extractor", ""),
                    "strength": getattr(
                        getattr(extractor, "detection_strength", None), "name", "?"
                    ),
                    "documented_as": reference["name"] if reference else None,
                    "era": (reference or {}).get("era"),
                    "size": (reference or {}).get("size"),
                    "identifier": (reference or {}).get("identifier"),
                    "summary": (reference or {}).get("summary"),
                }
            )
    rows.sort(key=lambda row: (row["manufacturer"], row["family"]))
    return rows


def _registry_coverage() -> list[dict]:
    """What the extractors themselves declare, grouped by manufacturer.

    This is the real answer to "what can it recognise": 38 extractors announce
    104 family identifiers. The curated table is *documentation* (era, vehicles,
    identifiers) and covers a subset — mostly Bosch — so showing it alone would
    understate the engine.
    """
    coverage: dict[str, dict] = {}
    for extractor in EXTRACTORS:
        manufacturer = extractor.name or "unknown"
        entry = coverage.setdefault(
            manufacturer, {"manufacturer": manufacturer, "extractors": 0, "families": []}
        )
        entry["extractors"] += 1
        for family in extractor.supported_families or []:
            if family not in entry["families"]:
                entry["families"].append(family)
    for entry in coverage.values():
        entry["families"].sort()
        entry["count"] = len(entry["families"])
    return sorted(coverage.values(), key=lambda item: (-item["count"], item["manufacturer"]))


@register(
    "families",
    kind="command",
    description=(
        "List the supported ECU families as a reference (era, typical size, "
        "vehicles, identifier). Optionally one family by name or alias."
    ),
    input_schema={
        "name": {
            "type": "str",
            "description": "One family by name or alias (case-insensitive).",
        },
    },
)
def families(params: dict) -> dict:
    name = (params or {}).get("name")
    if not name:
        return {
            "families": [dict(entry) for entry in FAMILIES],
            "count": len(FAMILIES),
            "registry": _registry_coverage(),
            "declared": _declared_families(),
            "family_names": len({name for entry in _registry_coverage() for name in entry["families"]}),
            # The reference *groups* families: 38 extractors answer to these 15
            # documented entries. Saying "15 families" without this would read as
            # "the tool recognises 15 things", which is wrong.
            "extractors": len(EXTRACTORS),
        }
    entry = find_family(str(name))
    if entry is None:
        raise GuardError(f"unknown family: {name!r}")
    return {"families": [dict(entry)], "count": 1, "extractors": len(EXTRACTORS)}
