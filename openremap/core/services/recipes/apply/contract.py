"""
openremap.core.services.recipes.apply.contract — read, check and verify what a
recipe declares about itself.

Two jobs, both pure (bytes/dicts, no I/O):

1. **Structural checks** (A1–A5, A12 in the rules document): is this a recipe,
   is it schema **5.0** (the only schema this engine supports), is it free of
   sections/fields we do not know, and does its declared ``contract`` agree
   with its own payload?
2. **One shape** — :class:`Contract` — so the engine reads the trust facts from
   a single object instead of poking at the JSON in five places.

**Legacy is not supported here.** A 4.x recipe is refused with a pointer to the
deprecated ``openremap tune`` command: deriving a contract from a stamp and
absent sections is exactly the guessing this engine exists to remove. The old
command keeps those recipes working; this engine only speaks 5.0.

Everything that cannot be verified raises :class:`ApplyRefused`, which carries
the rule id so the report can name the rule that stopped it.

See docs/internal/spec/recipe-5.0.md and tune-strict-rules.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SUPPORTED_MAJOR = 5
LEGACY_MAJOR = 4
MIN_SCHEMA = (4, 3)  # anything older cannot be read by the old command either

#: Optional top-level sections, in canonical (sorted) order. ``sections`` in a
#: 5.0 contract must equal the subset that is actually present.
OPTIONAL_SECTIONS = ("maps", "regions")

#: Every field must have a consumer — a field nobody reads is a field that can
#: only go stale or lie. (Checksum data was removed from the recipe for exactly
#: this reason: nothing in apply could act on it, and a wrong region would have
#: silently dropped real tune bytes.)
CONTRACT_FIELDS = (
    "identity_floor",
    "anchors_unique",
    "sections",
)

#: A recipe may always demand a stricter identity than its anchors need.
FLOOR_RANK = {"any": 0, "firmware": 1, "file": 2}

#: Instruction fields that decide *what* is written and *where*.
INSTRUCTION_SEMANTIC = ("offset", "size", "ob", "mb", "ctx", "context_after")
#: Instruction fields that are for humans and reports.
INSTRUCTION_ANNOTATION = (
    "offset_hex",
    "context_size",
    "ctx_entropy",
    "ctx_unique",
    "ctx_expanded",
    "description",
    "flags",
    "region",
    "needs_review",
)

_TOP_LEVEL = frozenset(
    {
        "type",
        "schema_version",
        "contract",
        "producer",
        "source",
        "tune",
        "fingerprint",
        "author",
        "metadata",
        "statistics",
        "instructions",
        "maps",
        "regions",
    }
)

_HEX_RE = re.compile(r"^[0-9A-Fa-f]*$")


# ---------------------------------------------------------------------------
# Refusal
# ---------------------------------------------------------------------------


class ApplyRefused(ValueError):
    """An *expected* refusal — not a bug.

    ``check`` is the rule id from the rules document (``"A2"``, ``"Γ4"`` …), so
    the report and the CLI can say precisely which rule stopped the apply.
    """

    def __init__(
        self,
        check: str,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.check = check
        self.detail = dict(detail or {})


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    """The identity/trust facts a 5.0 recipe declares, verified against itself."""

    schema: str
    identity_floor: str
    anchors_unique: bool
    sections: tuple[str, ...]
    source_identity: Mapping[str, Any]

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "identity_floor": self.identity_floor,
            "anchors_unique": self.anchors_unique,
            "sections": list(self.sections),
        }


# ---------------------------------------------------------------------------
# Declared source facts (schema 5.0 only)
# ---------------------------------------------------------------------------


def _source(recipe: Mapping[str, Any]) -> Mapping[str, Any]:
    source = recipe.get("source")
    return source if isinstance(source, Mapping) else {}


def declared_size(recipe: Mapping[str, Any]) -> int | None:
    """The stock file size the recipe was built from, if declared."""
    size = _source(recipe).get("size")
    return int(size) if size is not None else None


def declared_sha256(recipe: Mapping[str, Any]) -> str | None:
    """The stock file's SHA-256 the recipe was built from, if declared."""
    sha = _source(recipe).get("sha256")
    return str(sha) if sha else None


def declared_match_key(recipe: Mapping[str, Any]) -> str | None:
    """The stock binary's ``match_key``, if declared."""
    identity = _source(recipe).get("identity")
    if isinstance(identity, Mapping) and identity.get("match_key"):
        return str(identity["match_key"])
    return None


def _source_identity(recipe: Mapping[str, Any]) -> Mapping[str, Any]:
    identity = _source(recipe).get("identity")
    return identity if isinstance(identity, Mapping) else {}


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def _version_tuple(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return None
    try:
        parts = tuple(int(p) for p in str(raw).split("."))
    except (TypeError, ValueError):
        return None
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _check_type(recipe: Mapping[str, Any]) -> None:
    if recipe.get("type") != "recipe":
        raise ApplyRefused(
            "A1",
            "This file is not a recipe "
            f"(type={recipe.get('type')!r} instead of 'recipe'). "
            "Make sure you gave a .remap file and not, say, the output of identify.",
        )


def _check_schema(recipe: Mapping[str, Any]) -> str:
    """A2/A3 — a 5.x recipe, and nothing else."""
    raw = recipe.get("schema_version") or recipe.get("format_version")
    if raw is None:
        raise ApplyRefused(
            "A2",
            "The recipe does not declare a schema_version. Recipes from openremap "
            "< 0.5.0 cannot be read — cook it again.",
        )
    version = _version_tuple(raw)
    if version is None:
        raise ApplyRefused(
            "A2", f"Unrecognised schema_version: {raw!r} (expected something like '5.0')."
        )
    if version < MIN_SCHEMA:
        raise ApplyRefused(
            "A2",
            f"Schema {raw} is older than the minimum this build reads "
            f"({MIN_SCHEMA[0]}.{MIN_SCHEMA[1]}) — cook the recipe again.",
        )
    major = version[0]
    if major == LEGACY_MAJOR:
        raise ApplyRefused(
            "A2",
            f"The recipe is schema {raw}; this engine reads 5.0 only. 4.x recipes "
            "are no longer supported — cook the pair again to get a 5.0 recipe.",
            {"schema": str(raw)},
        )
    if major != SUPPORTED_MAJOR:
        raise ApplyRefused(
            "A3",
            f"Schema {raw} is a newer format (major {major}). "
            "Upgrade OpenRemap to read it.",
        )
    return str(raw)


def _check_known_keys(recipe: Mapping[str, Any]) -> None:
    unknown = sorted(k for k in recipe if k not in _TOP_LEVEL)
    if unknown:
        raise ApplyRefused(
            "A4",
            "The recipe has sections this build does not know: "
            + ", ".join(unknown)
            + ". It is not applied, because an unknown section can change the meaning.",
            {"unknown": unknown},
        )


def _check_instructions(recipe: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    instructions = recipe.get("instructions")
    if not isinstance(instructions, list) or not instructions:
        raise ApplyRefused("A12", "The recipe contains no instructions.")

    required = set(INSTRUCTION_SEMANTIC) | {"ctx_unique"}
    allowed = required | set(INSTRUCTION_ANNOTATION)
    for index, inst in enumerate(instructions, 1):
        if not isinstance(inst, Mapping):
            raise ApplyRefused("A12", f"Instruction #{index} is not an object.")
        unknown = sorted(k for k in inst if k not in allowed)
        if unknown:
            raise ApplyRefused(
                "A4",
                f"Instruction #{index} has fields this build does not know: "
                + ", ".join(unknown),
                {"index": index, "unknown": unknown},
            )
        missing = sorted(k for k in required if k not in inst)
        if missing:
            raise ApplyRefused(
                "A12",
                f"Instruction #{index} is missing: " + ", ".join(missing),
                {"index": index, "missing": missing},
            )
        for field in ("ob", "mb", "ctx", "context_after"):
            value = inst.get(field)
            if not isinstance(value, str) or not _HEX_RE.match(value) or len(value) % 2:
                raise ApplyRefused(
                    "A12",
                    f"Instruction #{index}: field {field} is not valid hex.",
                    {"index": index, "field": field},
                )
        if not isinstance(inst.get("ctx_unique"), bool):
            raise ApplyRefused(
                "A12",
                f"Instruction #{index}: ctx_unique must be true/false "
                "(the contract's anchors_unique is verified against it).",
                {"index": index},
            )
    return instructions


# ---------------------------------------------------------------------------
# Derivation of what the contract must say
# ---------------------------------------------------------------------------


def _derive_anchors_unique(instructions: Sequence[Mapping[str, Any]]) -> bool:
    return all(bool(inst["ctx_unique"]) for inst in instructions)


def _derive_sections(recipe: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(s for s in OPTIONAL_SECTIONS if s in recipe))


def _floor_for(anchors_unique: bool) -> str:
    return "any" if anchors_unique else "firmware"


def _check_contract(
    recipe: Mapping[str, Any], instructions: Sequence[Mapping[str, Any]]
) -> Contract:
    declared = recipe.get("contract")
    if not isinstance(declared, Mapping):
        raise ApplyRefused("A5", "A 5.0 recipe without a contract section.")

    unknown = sorted(k for k in declared if k not in CONTRACT_FIELDS)
    if unknown:
        raise ApplyRefused(
            "A4", "The contract has fields this build does not know: " + ", ".join(unknown)
        )
    missing = [k for k in CONTRACT_FIELDS if k not in declared]
    if missing:
        raise ApplyRefused("A5", "The contract is missing fields: " + ", ".join(missing))

    floor = declared["identity_floor"]
    if floor not in FLOOR_RANK:
        raise ApplyRefused("A5", f"Unknown identity_floor: {floor!r}.")

    derived_unique = _derive_anchors_unique(instructions)
    if bool(declared["anchors_unique"]) != derived_unique:
        raise ApplyRefused(
            "A5",
            "The contract declares anchors_unique="
            f"{bool(declared['anchors_unique'])} but the instructions say "
            f"{derived_unique}.",
        )

    derived_floor = _floor_for(derived_unique)
    if FLOOR_RANK[floor] < FLOOR_RANK[derived_floor]:
        raise ApplyRefused(
            "A5",
            f"The contract declares identity_floor={floor!r}, looser than the "
            f"anchors allow ({derived_floor!r}).",
        )

    derived_sections = _derive_sections(recipe)
    declared_sections = tuple(sorted(str(s) for s in declared["sections"]))
    if declared_sections != derived_sections:
        raise ApplyRefused(
            "A5",
            "The contract declares sections="
            f"{list(declared_sections)} but the recipe contains {list(derived_sections)}.",
        )

    return Contract(
        schema=str(recipe.get("schema_version")),
        identity_floor=floor,
        anchors_unique=derived_unique,
        sections=derived_sections,
        source_identity=_source_identity(recipe),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def read_contract(recipe: Mapping[str, Any]) -> Contract:
    """Run the structural checks (A1–A5, A12) and return the recipe's contract.

    Raises:
        ApplyRefused: with the rule id that stopped it.
    """
    if not isinstance(recipe, Mapping):
        raise ApplyRefused("A1", "The recipe is not a JSON object.")
    _check_type(recipe)
    schema = _check_schema(recipe)
    _check_known_keys(recipe)
    instructions = _check_instructions(recipe)
    contract = _check_contract(recipe, instructions)
    assert contract.schema == schema
    return contract
