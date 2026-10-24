"""
The cook report — structured and JSON-safe.

Mirrors the apply side: the service decides what a report *is*, the CLI decides
how to print it. Plain dicts, no classes, so it serialises as-is.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["add_note", "flags_by_kind", "new_report", "refuse"]

PASS = "pass"
REFUSE = "refuse"
INFO = "info"

#: What the operator should do next, by rule id.
NEXT_STEP = {
    "size": "The two files must be the same size — they are not the same image.",
    "identity": "The two files are different firmwares — no recipe can come from them.",
    "unique": (
        "Some anchors are not unique (blank flash/padding), so a recipe built this "
        "way could only be applied to this exact firmware. Use cook-forced to "
        "accept that trade, or leave this pair alone."
    ),
    "empty": "The two versions are identical — there is nothing to record.",
    "identity_required": (
        "A recipe with non-unique anchors can only be applied where the identity "
        "verifies, and this pair is not recognised. Check that both files belong "
        "to a known family."
    ),
}


def new_report(
    *, command: str, target_stock: str, target_tuned: str, require_unique: bool
) -> dict:
    return {
        "command": command,
        "mode": {
            "require_unique": require_unique,
            "exclusions": "none",
        },
        "files": {"stock": target_stock, "tuned": target_tuned},
        "identity": {},
        "counts": {},
        "flags": {},
        "regions": {},
        "contract": {},
        "warnings": [],
        "notes": [],
        "result": {"produced": False},
        "refusal": None,
        "next_step": None,
    }


def add_note(report: dict, message: str) -> None:
    report["notes"].append(message)


def flags_by_kind(instructions: Sequence[Mapping[str, Any]]) -> dict:
    """Count instruction flags by kind — one line per kind, not one per flag."""
    counts: Counter[str] = Counter()
    for inst in instructions:
        for flag in inst.get("flags") or []:
            kind = flag.get("kind")
            if kind:
                counts[str(kind)] += 1
    return dict(sorted(counts.items()))


def refuse(report: dict, rule: str, message: str) -> dict:
    report["refusal"] = {"rule": rule, "message": message}
    report["next_step"] = NEXT_STEP.get(rule)
    report["result"] = {"produced": False}
    return report
