"""
The apply report — structured and JSON-safe.

The CLI decides how to print it; this module only decides what a report *is*.
The shape is deliberately not byte-compatible with the deprecated three-phase
``tune`` report: parity with the old engine is asserted on the data (per
instruction status, shift, patched sha256), not on the JSON.

Verdicts
--------
``pass``    the check ran and the target satisfies it
``refuse``  the check failed and the apply stopped
``info``    the check produced information only; it never blocks
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PASS = "pass"
REFUSE = "refuse"
INFO = "info"

#: What the operator should do next, by rule id. Commands named here must
#: actually exist today — the pointer is the whole point of a refusal.
NEXT_STEP = {
    "A1": "Give it a .remap file — not the output of identify, not some other JSON.",
    "A2": "This engine reads schema 5.0 only. 4.x recipes are no longer supported — cook the pair again to get a 5.0 recipe.",
    "A3": "Upgrade OpenRemap: the recipe is written in a newer format than this build reads.",
    "A4": "The recipe carries fields this build does not know — cook it again with the same "
          "version that will read it.",
    "A5": "The recipe's contract disagrees with its own contents — do not trust it; cook it again.",
    "A12": "The recipe is incomplete — cook it again.",
    "Γ1": "The target is a different size from the recipe's source — check that you picked the "
          "right file.",
    "Γ2": "Different firmware. Applying across firmwares is possible but deliberate — no apply "
          "command on this ladder will do it.",
    "Γ3": "A different copy of the file. A command that checks the firmware rather than the exact "
          "file accepts this.",
    "Γ4": "The recipe's original bytes are not in place — this target is not the file the recipe "
          "describes.",
    "Γ5": "This target already has this recipe's changes.",
    "Γ6": "The anchor was not found in the target — probably a different revision.",
    "Γ7": "The anchor was found displaced — the layout is not the same.",
    "Δ1": "Internal error: the result does not verify. Report it.",
    "Δ4": "Internal error: bytes were written outside the declared ranges. Report it.",
}


def new_report(
    *,
    command: str,
    identity_policy: str,
    target_name: str,
    recipe_name: str,
) -> dict:
    """A fresh report skeleton."""
    return {
        "command": command,
        "policy": {
            "identity": identity_policy,
            "shift": "must be zero",
            "ambiguity": "reported",
        },
        "files": {"target": target_name, "recipe": recipe_name},
        "checks": [],
        "schema": None,
        "identity": {},
        "sizes": {},
        "anchors": {},
        "instructions": {},
        "regions": {},
        "result": {"applied": False},
        "refusal": None,
        "next_step": None,
    }


def add_check(
    report: dict,
    check_id: str,
    name: str,
    verdict: str,
    detail: str | Any = "",
) -> None:
    """Record one check's outcome."""
    report["checks"].append(
        {
            "id": check_id,
            "name": name,
            "verdict": verdict,
            "detail": detail if isinstance(detail, str) else str(detail),
        }
    )


def refuse(report: dict, exc: Exception) -> dict:
    """Mark the report refused, using the rule id carried by *exc*."""
    check = getattr(exc, "check", "?")
    report["refusal"] = {
        "check": check,
        "message": str(exc),
        "detail": dict(getattr(exc, "detail", {}) or {}),
    }
    report["next_step"] = NEXT_STEP.get(check)
    report["result"] = {"applied": False}
    return report


#: Printed with every successful apply, by every consumer.
#:
#: Three claims this tool cannot make on the operator's behalf: what it does not do
#: to the file (correct checksums), who has to look at the result before it reaches
#: a vehicle, and the boundary of the use it supports — right to repair and track
#: racing, not an everyday car. Kept here, next to the disclosure it qualifies, so
#: the CLI and the TUI cannot drift apart on it.
DISCLAIMER = (
    "OpenRemap does not correct checksums automatically. A tune that will run in "
    "a real vehicle must be reviewed by a dedicated tuner shop. Intended for "
    "right to repair and track racing only — not for modifying an everyday car."
)


def checksum_disclosure(patched: bytes) -> dict:
    """What can honestly be said about checksums in the file we just produced.

    This engine writes the calibrated bytes and **never** a stored checksum — the
    checksum side of this package is detect-only by design. So a patched file is
    *expected* to be stale wherever the family has a checksum, and staying silent
    about it is the one trap left in the write path: the file verifies byte for
    byte against what the recipe promised and is still not flashable.

    The verdict is measured, not boilerplate — one detector pass over the patched
    bytes (tens of ms, not the brute-force sweep), giving three honest answers:

    ``ok``    every scheme we know how to verify still verifies
    ``fail``  at least one is now stale — the patch broke it
    ``skip``  no scheme known for this family, so nobody checked anything
    """
    from openremap.core.services.health import check_checksums

    check = check_checksums(patched)
    return {
        # Never true, and never will be: correcting a checksum needs the family's
        # algorithm, which detect-only deliberately does not implement.
        "corrected": False,
        "status": check.status,  # ok | fail | skip
        "message": check.message,
        "details": list(check.details),
        "check_with": "openremap checksum <written file>",
    }


def checks_table(report: Mapping[str, Any]) -> list[dict]:
    """Convenience: the checks as a list of plain dicts (already are)."""
    return [dict(c) for c in report.get("checks", [])]
