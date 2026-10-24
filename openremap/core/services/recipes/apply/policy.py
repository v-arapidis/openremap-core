"""
Identity policies — what an apply command guarantees about the target binary.

Three policies, strict to loose. A command names one policy; it has no flag
that loosens it afterwards.

===========  ==============================================================
``FILE``     The target must be byte-identical to the recipe's source
             (SHA-256 of ``source.sha256`` / ``ecu.sha256``).
``FIRMWARE`` The target must identify as the same firmware as the recipe's
             source (``match_key`` equal). Another car carrying the same
             software revision satisfies this; a different revision does not.
``ANY``      No identity requirement — byte-level checks only.
===========  ==============================================================

``tune-strict`` runs ``FIRMWARE``.

The policy is a *minimum*: a recipe may demand a stricter level than its
anchors need (see ``contract.identity_floor``), and a command may always be
stricter than the recipe's floor — never looser.
"""

from __future__ import annotations

from enum import Enum

from openremap.core.services.recipes.apply.contract import (
    FLOOR_RANK,
    ApplyRefused,
    Contract,
)

__all__ = [
    "IdentityPolicy",
    "check_floor",
    "check_identity",
    "describe_identity",
]


class IdentityPolicy(str, Enum):
    """How much identity an apply command demands of its target."""

    FILE = "file"
    FIRMWARE = "firmware"
    ANY = "any"

    @property
    def rank(self) -> int:
        return FLOOR_RANK[self.value]


def describe_identity(policy: IdentityPolicy) -> str:
    """One-line, operator-facing description of what *policy* guarantees."""
    return {
        IdentityPolicy.FILE: "the same file (SHA-256)",
        IdentityPolicy.FIRMWARE: "the same firmware (match_key)",
        IdentityPolicy.ANY: "no identity requirement",
    }[policy]


def check_floor(policy: IdentityPolicy, contract: Contract) -> None:
    """Refuse when *policy* is looser than the recipe's own floor.

    The recipe says what it needs (``identity_floor``); a command may ask for
    more, never for less.
    """
    if policy.rank < FLOOR_RANK[contract.identity_floor]:
        raise ApplyRefused(
            "Γ2",
            f"The recipe requires at least '{contract.identity_floor}' identity, "
            f"but this command asks for '{policy.value}'. Use a stricter command.",
            {"required_floor": contract.identity_floor, "policy": policy.value},
        )


def check_identity(
    policy: IdentityPolicy,
    *,
    recipe_match_key: str | None,
    target_match_key: str | None,
    recipe_sha256: str | None,
    target_sha256: str,
) -> None:
    """Raise ``ApplyRefused`` unless the target satisfies *policy*.

    Both sides must be identified: "cannot verify" is a refusal, not a pass —
    that is what makes this policy worth having.

    Raises:
        ApplyRefused: rule ``Γ2`` (identity) or ``Γ3`` (byte identity).
    """
    if policy is IdentityPolicy.ANY:
        return

    if policy is IdentityPolicy.FILE:
        if not recipe_sha256:
            raise ApplyRefused(
                "Γ3",
                "The recipe does not record the source's sha256, so file identity "
                "cannot be verified.",
            )
        if recipe_sha256 != target_sha256:
            raise ApplyRefused(
                "Γ3",
                "This command requires the same file: "
                f"recipe sha256={recipe_sha256[:16]}… · "
                f"target sha256={target_sha256[:16]}…",
                {"recipe_sha256": recipe_sha256, "target_sha256": target_sha256},
            )
        return

    # IdentityPolicy.FIRMWARE
    if not recipe_match_key:
        raise ApplyRefused(
            "Γ2",
            "The recipe does not record the source's match_key, so firmware "
            "identity cannot be verified. Use a looser command if you know the pair.",
        )
    if not target_match_key:
        raise ApplyRefused(
            "Γ2",
            "The target is not recognised, so firmware identity cannot be "
            "verified. Use a looser command if you know the pair.",
        )
    if recipe_match_key != target_match_key:
        raise ApplyRefused(
            "Γ2",
            f"Different firmware: recipe={recipe_match_key} · "
            f"target={target_match_key}.",
            {"recipe_match_key": recipe_match_key, "target_match_key": target_match_key},
        )
