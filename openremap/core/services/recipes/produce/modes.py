"""
Cook modes — what a recipe guarantees about the pair it was built from, and
therefore which apply commands may use it later.

Two modes, because the apply side has exactly one thing it can enforce: the
recipe's ``identity_floor``, which derives from anchor uniqueness.

===============  ==========================================================
``cook-strict``  Every anchor must be unique in the stock binary; the cook
                 refuses otherwise. Result: ``identity_floor: any`` — the
                 recipe is self-verifying, so *every* apply command may use
                 it, including the identity-free ones.
``cook-forced``  Non-unique anchors are accepted (a change inside blank flash
                 or padding). Result: ``identity_floor: firmware`` — only the
                 commands that verify ``match_key`` may apply it.
===============  ==========================================================

A third mode would only be justified by a recipe that must be applicable
**without** identity **and** cannot have unique anchors. Nothing in the corpus
needs that today; the measurement is in the changelog.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COOK_FORCED", "COOK_STRICT", "CookMode", "get_mode"]

STRICT_COMMAND = "cook-strict"
FORCED_COMMAND = "cook-forced"


@dataclass(frozen=True)
class CookMode:
    """One cook mode: a name, and the rule that decides anchor uniqueness."""

    command: str
    #: Refuse when any instruction's anchor is not unique in the stock binary.
    require_unique: bool
    #: What the operator should try when this mode refuses.
    looser_hint: str
    #: One-line description used by the reports and the CLI.
    summary: str


COOK_STRICT = CookMode(
    command=STRICT_COMMAND,
    require_unique=True,
    looser_hint=f"openremap {FORCED_COMMAND}",
    summary="unique anchors — applies to any dump of this family",
)

COOK_FORCED = CookMode(
    command=FORCED_COMMAND,
    require_unique=False,
    looser_hint="none (this is as loose as cooking gets)",
    summary="repeated anchors allowed — applying it needs the same firmware",
)

_MODES = {mode.command: mode for mode in (COOK_STRICT, COOK_FORCED)}


def get_mode(command: str) -> CookMode:
    """Look a mode up by its command name."""
    try:
        return _MODES[command]
    except KeyError:  # pragma: no cover - programmer error
        raise ValueError(
            f"unknown cook mode {command!r} (expected one of {sorted(_MODES)})"
        ) from None
