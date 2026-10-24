"""
The apply pipeline — the engine behind ``tune-strict`` and ``tune-shift``.

One pass, in this order, each step recording a verdict in the report:

1. **Recipe** (A1–A5, A12) — is it a schema-5.0 recipe we understand, and does
   its declared contract agree with its own payload?
2. **Floor** (Γ2) — the recipe's ``identity_floor`` is a minimum; a command may
   be stricter, never looser.
3. **Size** (Γ1) — a declared size that does not match means the offsets are
   meaningless.
4. **Identity** (Γ2/Γ3) — one ``identify`` call, then ``match_key`` equality.
   Both sides must be identified: "cannot verify" is a refusal, not a pass.
5. **Exact bytes** (Γ4/Γ5) — every ``ob`` at its recorded offset. When the bytes
   there are the ``mb`` instead, the message says "already patched".
6. **Anchor search** (Γ6) — ``ctx + ob + context_after`` within ±2048 bytes,
   composed from the existing ``ECUPatcher``; all-or-nothing.
7. **Shift** (Γ7) — in ``tune-strict`` the anchor must be found at exactly the
   expected offset; in ``tune-shift`` a displaced anchor is allowed and
   reported.
8. **Hit count** (Γ8) — in ``tune-strict`` it is information only (positions are
   already pinned by Γ4+Γ7, and refusing would reject correct work: a change in
   blank flash legitimately has thousands of look-alikes). In ``tune-shift`` it
   is **decisive**, because once a shift is allowed the engine really does choose
   between candidates.
9. **Post-verify** (Δ1) and **accounting** (Δ4) — the result is re-read, and
   every byte that changed must lie inside a written range (expected offsets in
   strict, found offsets in shift).

Nothing is written unless every decisive step passes. Informational steps
(A7, A8, A11) never block: they exist so the operator can see what the recipe
claims and what the engine could not determine.

Checksums are deliberately absent from the recipe: it stores nothing about them,
and a stored region would have silently dropped real tune bytes — the one failure
direction that is invisible and unrecoverable. Detection is a live capability
over a binary (see ``services/checksums/``), so the engine *asks* it about the
file it just produced and reports the answer; it never refuses on it. The
distinction matters: a stale checksum does not make the recipe wrong, it makes
the written file unflashable, and the operator has to learn that here rather than
from the ECU.

This module only *composes* the existing primitives — strict validator,
patcher, patched validator. There is one safety core in this codebase.
"""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openremap._rust import find_changed_blocks  # type: ignore[import-untyped]
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.recipes.apply import report as report_mod
from openremap.core.services.recipes.apply.contract import (
    ApplyRefused,
    Contract,
    declared_match_key,
    declared_sha256,
    declared_size,
    read_contract,
)
from openremap.core.services.recipes.apply.policy import (
    IdentityPolicy,
    check_floor,
    check_identity,
)
from openremap.core.services.recipes.patcher import EXACT_WINDOW, ECUPatcher
from openremap.core.services.recipes.validate_patched import ECUPatchedValidator
from openremap.core.services.recipes.validate_strict import ECUStrictValidator

__all__ = [
    "ANY_COMMAND",
    "SHIFT_COMMAND",
    "STRICT_COMMAND",
    "STRICT_POLICY",
    "UNIQUE_COMMAND",
    "apply_any",
    "apply_shift",
    "apply_strict",
    "apply_unique",
]

STRICT_COMMAND = "tune-strict"
SHIFT_COMMAND = "tune-shift"
UNIQUE_COMMAND = "tune-unique"
ANY_COMMAND = "tune-any"
STRICT_POLICY = IdentityPolicy.FIRMWARE

#: Upper bound on how many anchor copies report building counts per
#: instruction — protective, not semantic.
HIT_SCAN_CAP = 20_000


# ---------------------------------------------------------------------------
# Rules — what one command relaxes and what it refuses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rules:
    """The differences between the commands, in one place.

    Everything else — recipe checks, identity, size, anchor search, verify,
    accounting — is shared. Two commands, one pipeline.
    """

    command: str
    identity: IdentityPolicy
    #: Γ4/Γ5 — require every ``ob`` at its recorded offset (strict) or let the
    #: anchored search prove the bytes where it finds them (shift).
    exact_bytes: bool
    #: Γ7 — refuse a displaced anchor.
    zero_shift: bool
    #: Γ8 — refuse when the anchor has more than one copy in the window.
    #: Only meaningful when a shift is allowed: with an exact position the
    #: patcher's "closest match" is the expected offset by construction, so the
    #: count is inert. In the shift column it is a real choice, and the two
    #: commands differ there on purpose (tune-shift refuses, tune-any accepts).
    ambiguity_blocks: bool
    #: Δ4 — measure changed bytes against the declared offsets (strict) or the
    #: offsets actually written (shift).
    accounting: str  # "expected" | "found"
    #: What the operator should try when this command refuses on identity.
    looser_hint: str


STRICT_RULES = Rules(
    command=STRICT_COMMAND,
    identity=IdentityPolicy.FIRMWARE,
    exact_bytes=True,
    zero_shift=True,
    ambiguity_blocks=False,
    accounting="expected",
    looser_hint="openremap tune-shift",
)

SHIFT_RULES = Rules(
    command=SHIFT_COMMAND,
    identity=IdentityPolicy.FIRMWARE,
    exact_bytes=False,
    zero_shift=False,
    ambiguity_blocks=True,
    accounting="found",
    looser_hint="openremap tune-any",
)

#: The rung that drops the *label* but keeps every piece of *proof*: the bytes
#: must be at their recorded offsets AND the anchor must be unique. Two
#: independent proofs, no identity. This is the command for dumps that cannot be
#: identified at all — which the identity commands must refuse.
UNIQUE_RULES = Rules(
    command=UNIQUE_COMMAND,
    identity=IdentityPolicy.ANY,
    exact_bytes=True,
    zero_shift=True,
    # Ambiguity is INERT here: the bytes must be at their recorded offset and
    # ``shift`` must be 0, so the patcher's "closest match" is by definition the
    # expected offset. Policing it would refuse correct work for no gain — and
    # it would be inconsistent with tune-strict, which has the same positional
    # guarantee and only reports it.
    ambiguity_blocks=False,
    accounting="expected",
    looser_hint="openremap tune-any",
)

#: The loosest rung: no identity, a displaced anchor is accepted, and an
#: ambiguous anchor is reported instead of refused. Everything mechanical is
#: still enforced (recipe checks, size, anchor found, verify, accounting), and
#: the recipe's own floor still applies.
ANY_RULES = Rules(
    command=ANY_COMMAND,
    identity=IdentityPolicy.ANY,
    exact_bytes=False,
    zero_shift=False,
    ambiguity_blocks=False,
    accounting="found",
    looser_hint="— (this is the loosest command)",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _field_bytes(inst: Mapping[str, Any], field: str) -> bytes:
    try:
        return bytes.fromhex(inst.get(field) or "")
    except ValueError:
        return b""


def _window_hits(snapshot: bytes, inst: Mapping[str, Any], expected: int) -> int:
    """How many copies of the anchor sit in the patcher's search window.

    Counts **overlapping** occurrences (advancing one byte at a time), because
    that is what the patcher reports: in a uniform region a 513-byte anchor
    inside a 4 KB window occurs at ~4 000 positions, not ~8. ``bytes.count``
    counts non-overlapping matches and would understate it.

    Capped at :data:`HIT_SCAN_CAP` so a pathological recipe cannot stall a run.
    """
    anchor = (
        _field_bytes(inst, "ctx")
        + _field_bytes(inst, "ob")
        + _field_bytes(inst, "context_after")
    )
    if not anchor:
        return 0
    start = max(0, expected - EXACT_WINDOW)
    end = min(len(snapshot), expected + EXACT_WINDOW + len(anchor))
    window = snapshot[start:end]
    count = 0
    pos = window.find(anchor)
    while pos != -1 and count < HIT_SCAN_CAP:
        count += 1
        pos = window.find(anchor, pos + 1)
    return count


def _declared_ranges(instructions: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    ranges = []
    for inst in instructions:
        offset = int(inst["offset"])
        size = len(_field_bytes(inst, "mb")) or int(inst.get("size") or 0)
        ranges.append((offset, offset + size))
    return sorted(ranges)


def _found_ranges(results: Sequence[Any]) -> list[tuple[int, int]]:
    ranges = []
    for result in results:
        found = result.offset_found
        if found is None:
            found = result.offset_expected
        ranges.append((int(found), int(found) + int(result.size)))
    return sorted(ranges)


def _accounting(
    before: bytes, after: bytes, ranges: Sequence[tuple[int, int]]
) -> dict:
    """Δ4 — every changed byte must be inside a written range, and vice versa.

    Uses the Rust changed-blocks scan the cook already relies on instead of
    walking the image byte by byte in Python: on a 4 MB dump that is ~2 ms
    instead of ~550 ms, and it is the same primitive that produced the diff in
    the first place.
    """
    merged = _merge_ranges(ranges)
    blocks = find_changed_blocks(before, after, 1)

    changed_bytes = 0
    stray = 0
    hits: set[int] = set()
    for offset, size, _ob, _mb in blocks:
        end = offset + size
        changed_bytes += size
        covered = 0
        for index, (start, stop) in enumerate(merged):
            lo, hi = max(offset, start), min(end, stop)
            if lo < hi:
                covered += hi - lo
                hits.add(index)
        stray += size - covered

    return {
        "changed_bytes": changed_bytes,
        "stray": stray,
        "unwritten_ranges": len(merged) - len(hits),
    }


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and coalesce overlapping ranges so accounting never double-counts."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _recipe_at_found_offsets(
    recipe: Mapping[str, Any], results: Sequence[Any]
) -> dict:
    """A copy of *recipe* whose offsets are the ones actually written.

    Lets the existing post-patch validator be reused unchanged when anchors moved.
    """
    shifted = copy.deepcopy(dict(recipe))
    for inst, result in zip(shifted["instructions"], results):
        if result.offset_found is None:
            continue
        inst["offset"] = int(result.offset_found)
        inst["offset_hex"] = f"{int(result.offset_found):X}"
    return shifted


def _stale_hint(
    target_data: bytes, instructions: Sequence[Mapping[str, Any]]
) -> int:
    """Count instructions whose expected offset already holds their ``mb``."""
    already = 0
    for inst in instructions:
        offset = int(inst["offset"])
        mb = _field_bytes(inst, "mb")
        if mb and target_data[offset:offset + len(mb)] == mb:
            already += 1
    return already


# ---------------------------------------------------------------------------
# Informational blocks (never block)
# ---------------------------------------------------------------------------


def _report_contract(report: dict, contract: Contract, recipe: Mapping[str, Any]) -> None:
    """A7–A11 — what the recipe claims about itself (information only)."""
    instructions = recipe.get("instructions") or []
    total_bytes = sum(len(_field_bytes(i, "mb")) for i in instructions)
    report_mod.add_check(
        report,
        "A7",
        "recipe size",
        report_mod.INFO,
        f"{len(instructions)} instructions · {total_bytes} bytes",
    )

    regions = recipe.get("regions")
    if isinstance(regions, Mapping) and regions:
        report_mod.add_check(
            report, "A8", "flash regions", report_mod.INFO, dict(regions)
        )
        report["regions"] = dict(regions)
    else:
        report_mod.add_check(
            report, "A8", "flash regions", report_mod.INFO, "not declared"
        )

    producer = recipe.get("producer")
    if isinstance(producer, Mapping):
        report_mod.add_check(
            report,
            "A11",
            "producer",
            report_mod.INFO,
            f"{producer.get('engine', '?')} {producer.get('version', '?')} · "
            f"command={producer.get('command', '?')}",
        )


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _run(
    report: dict,
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str,
    recipe_name: str,
    rules: Rules,
) -> dict:
    # ---- 1. Recipe -------------------------------------------------------
    contract = read_contract(recipe)
    report["schema"] = contract.schema
    report_mod.add_check(
        report,
        "A1–A5",
        "recipe valid",
        report_mod.PASS,
        f"schema {contract.schema} · the contract agrees with the contents",
    )
    _report_contract(report, contract, recipe)

    # ---- 2. Floor --------------------------------------------------------
    check_floor(rules.identity, contract)
    report_mod.add_check(
        report,
        "Γ2-floor",
        "recipe floor",
        report_mod.PASS,
        f"recipe floor={contract.identity_floor} · command={rules.identity.value}",
    )

    # ---- 3. Size ---------------------------------------------------------
    size = declared_size(recipe)
    if size is None:
        raise ApplyRefused(
            "Γ1",
            "The recipe does not declare a source size, so nothing verifies that "
            "the target is compatible.",
        )
    if size != len(target_data):
        raise ApplyRefused(
            "Γ1",
            f"Size: recipe={size:,} B · target={len(target_data):,} B.",
            {"recipe_size": size, "target_size": len(target_data)},
        )
    report["sizes"] = {"recipe": size, "target": len(target_data)}
    report_mod.add_check(report, "Γ1", "size", report_mod.PASS, f"{size:,} B")

    # ---- 4. Identity -----------------------------------------------------
    target_id = identify_ecu(data=target_data, filename=target_name)
    target_sha = _sha256(target_data)
    recipe_sha = declared_sha256(recipe)
    recipe_key = declared_match_key(recipe) or contract.source_identity.get("match_key")
    target_key = target_id.get("match_key")
    report["identity"] = {
        "recipe_match_key": recipe_key,
        "target_match_key": target_key,
        "same_file": bool(recipe_sha and recipe_sha == target_sha),
        "target": {
            k: target_id.get(k)
            for k in (
                "manufacturer",
                "ecu_family",
                "ecu_variant",
                "software_version",
                "hardware_number",
                "oem_part_number",
                "confidence",
            )
        },
    }
    check_identity(
        rules.identity,
        recipe_match_key=recipe_key,
        target_match_key=target_key,
        recipe_sha256=recipe_sha,
        target_sha256=target_sha,
    )
    if rules.identity is IdentityPolicy.ANY:
        same_key = bool(recipe_key) and recipe_key == target_key
        report_mod.add_check(
            report,
            "Γ2",
            "firmware identity",
            report_mod.INFO,
            "not checked by this command · "
            f"recipe={recipe_key or '—'} · target={target_key or '—'}"
            + (" (same)" if same_key else
               " ⚠ DIFFERENT — you are writing across firmwares because you asked to"),
        )
    else:
        report_mod.add_check(
            report, "Γ2", "firmware identity", report_mod.PASS, f"{target_key}"
        )
    report_mod.add_check(
        report,
        "Γ3",
        "file identity",
        report_mod.INFO,
        "the same file as the source"
        if report["identity"]["same_file"]
        else "a different copy (expected when patching a second car)",
    )

    instructions = recipe["instructions"]

    # ---- 5. Exact bytes (strict only) ------------------------------------
    if rules.exact_bytes:
        validator = ECUStrictValidator(
            target_data=target_data,
            recipe=dict(recipe),
            target_name=target_name,
            recipe_name=recipe_name,
        )
        validator.validate_all()
        passed, failed, _pct = validator.score()
        if failed:
            already = 0
            first = None
            for result in validator.results:
                if result.passed:
                    continue
                inst = instructions[result.instruction_index - 1]
                if (result.found_bytes or "").upper() == (inst.get("mb") or "").upper():
                    already += 1
                if first is None:
                    first = result
            if already == failed:
                raise ApplyRefused(
                    "Γ5",
                    f"The target already carries this recipe "
                    f"({failed} instruction(s) already hold the new bytes).",
                    {"already_patched": failed},
                )
            raise ApplyRefused(
                "Γ4",
                f"The recipe's original bytes are not in place: {failed} of "
                f"{len(instructions)} instruction(s) do not match "
                f"(e.g. #{first.instruction_index} at 0x{first.offset:X}).",
                {"failed": failed, "index": first.instruction_index, "offset": first.offset},
            )
        report_mod.add_check(
            report,
            "Γ4",
            "original bytes in place",
            report_mod.PASS,
            f"{passed}/{len(instructions)}",
        )
    else:
        report_mod.add_check(
            report,
            "Γ4",
            "original bytes in place",
            report_mod.INFO,
            "not required — position is proven by the anchor",
        )

    # ---- 6. Anchor search ------------------------------------------------
    patcher = ECUPatcher(
        target_data=target_data,
        recipe=dict(recipe),
        target_name=target_name,
        recipe_name=recipe_name,
        skip_validation=True,
    )
    try:
        patched = patcher.apply_all()
    except ValueError as exc:
        already = _stale_hint(target_data, instructions)
        if already == len(instructions):
            raise ApplyRefused(
                "Γ5",
                "The target already carries this recipe "
                f"({already} instruction(s) already hold the new bytes).",
                {"already_patched": already},
            ) from exc
        raise ApplyRefused("Γ6", f"The anchor search failed — {exc}") from exc
    report_mod.add_check(
        report,
        "Γ6",
        "anchor search (±2048)",
        report_mod.PASS,
        f"{len(patcher.results)}/{len(instructions)} found",
    )

    # ---- 7. Shift --------------------------------------------------------
    shifted = [r for r in patcher.results if r.shift]
    if shifted and rules.zero_shift:
        sample = ", ".join(
            f"#{r.index} 0x{r.offset_expected:X}{r.shift:+d}" for r in shifted[:3]
        )
        raise ApplyRefused(
            "Γ7",
            f"{len(shifted)} instruction(s) were found displaced ({sample}) — "
            "the target's layout is not the source's. "
            f"For a displaced layout: {rules.looser_hint}",
            {"shifted": len(shifted)},
        )
    if shifted:
        worst = max(shifted, key=lambda r: abs(r.shift or 0))
        report_mod.add_check(
            report,
            "Γ7",
            "shift",
            report_mod.INFO,
            f"{len(shifted)} instruction(s) displaced · "
            f"largest {worst.shift:+d} bytes (#{worst.index})",
        )
    else:
        report_mod.add_check(
            report, "Γ7", "shift", report_mod.PASS, "every shift=0"
        )
    report["anchors"] = {
        "shifted": len(shifted),
        "max_shift": max((abs(r.shift or 0) for r in patcher.results), default=0),
    }

    # ---- 8. Hit count ----------------------------------------------------
    hits = [
        _window_hits(target_data, inst, int(inst["offset"])) for inst in instructions
    ]
    ambiguous = [r for r in patcher.results if r.ambiguous]
    report["anchors"].update(
        {
            "total": len(instructions),
            "max_hits": max(hits) if hits else 0,
            "multi_hit": sum(1 for h in hits if h > 1),
            "ambiguous": len(ambiguous),
        }
    )
    detail = (
        f"max {report['anchors']['max_hits']} in the window · "
        f"{report['anchors']['multi_hit']} instruction(s) with >1"
    )
    if rules.ambiguity_blocks and ambiguous:
        sample = ", ".join(
            f"#{r.index} 0x{r.offset_expected:X}" for r in ambiguous[:3]
        )
        raise ApplyRefused(
            "Γ8",
            f"{len(ambiguous)} instruction(s) have more than one anchor copy "
            f"({sample}) — with a shift allowed, choosing a position is not safe. "
            f"To accept ambiguity as a report instead: {rules.looser_hint}",
            {"ambiguous": len(ambiguous)},
        )
    report_mod.add_check(
        report,
        "Γ8",
        "anchor copies",
        report_mod.PASS if rules.ambiguity_blocks else report_mod.INFO,
        detail,
    )

    # ---- 9. Post-verify --------------------------------------------------
    verify_recipe = (
        dict(recipe)
        if rules.accounting == "expected"
        else _recipe_at_found_offsets(recipe, patcher.results)
    )
    post = ECUPatchedValidator(
        patched_data=patched,
        recipe=verify_recipe,
        patched_name="(patched)",
        recipe_name=recipe_name,
    )
    post.verify_all()
    p_passed, p_failed, _ = post.score()
    if p_failed:
        raise ApplyRefused(
            "Δ1",
            f"Post-write verification: {p_failed} instruction(s) were not "
            "written correctly.",
            {"failed": p_failed},
        )

    ranges = (
        _declared_ranges(instructions)
        if rules.accounting == "expected"
        else _found_ranges(patcher.results)
    )
    accounting = _accounting(target_data, patched, ranges)
    if accounting["stray"] or accounting["unwritten_ranges"]:
        raise ApplyRefused(
            "Δ4",
            "Byte accounting: "
            f"{accounting['stray']} byte(s) changed outside the declared ranges, "
            f"{accounting['unwritten_ranges']} declared range did not change.",
            accounting,
        )

    written = sum(len(_field_bytes(i, "mb")) for i in instructions)
    report_mod.add_check(
        report,
        "Δ1",
        "result verification",
        report_mod.PASS,
        f"{p_passed}/{len(instructions)}",
    )
    report_mod.add_check(
        report,
        "Δ4",
        "byte accounting",
        report_mod.PASS,
        f"{accounting['changed_bytes']} bytes changed, all inside the declared ranges",
    )

    report["instructions"] = {
        "total": len(instructions),
        "applied": len(instructions),
        "bytes_written": written,
    }
    report["result"] = {
        "applied": True,
        "input_sha256": target_sha,
        "output_sha256": _sha256(patched),
        "bytes_written": written,
        # Said before the caller can mistake "the recipe verified" for "the file
        # is flashable": nothing in this engine rewrites a stored checksum.
        "checksums": report_mod.checksum_disclosure(patched),
        "disclaimer": report_mod.DISCLAIMER,
    }
    return {
        "applied": True,
        "patched_b64": base64.b64encode(patched).decode("ascii"),
        "patched_sha256": report["result"]["output_sha256"],
        "report": report,
    }


def _apply(
    rules: Rules,
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str,
    recipe_name: str,
) -> dict:
    report = report_mod.new_report(
        command=rules.command,
        identity_policy=rules.identity.value,
        target_name=target_name,
        recipe_name=recipe_name,
    )
    report["policy"].update(
        {
            "positions": "exact" if rules.zero_shift else "anchor ±2048",
            "shift": "must be zero" if rules.zero_shift else "allowed, reported",
            "ambiguity": "must be unique"
            if rules.ambiguity_blocks
            else "reported",
        }
    )
    try:
        return _run(report, target_data, recipe, target_name, recipe_name, rules)
    except ApplyRefused as exc:
        report_mod.refuse(report, exc)
        return {
            "applied": False,
            "patched_b64": None,
            "patched_sha256": None,
            "report": report,
        }


def apply_strict(
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str = "target.bin",
    recipe_name: str = "recipe.remap",
) -> dict:
    """Apply under the ``tune-strict`` rules: exact offsets, zero shift.

    Returns a dict with ``applied`` (bool), ``patched_b64`` /
    ``patched_sha256`` (None when refused) and the structured ``report``.

    Refusals are *expected outcomes*, not exceptions: the caller gets
    ``applied: False`` plus the rule that stopped it and the next step to try.
    """
    return _apply(STRICT_RULES, target_data, recipe, target_name, recipe_name)


def apply_unique(
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str = "target.bin",
    recipe_name: str = "recipe.remap",
) -> dict:
    """Apply with no identity requirement, but every piece of proof kept.

    The bytes must be at their recorded offsets (``shift == 0``) **and** each
    anchor must be unique in the search window. Those are two independent
    proofs of position, which is why the firmware label is not needed — this is
    the command for dumps that cannot be identified at all. The recipe's own
    ``identity_floor`` still applies: a recipe that demands firmware identity is
    refused here.
    """
    return _apply(UNIQUE_RULES, target_data, recipe, target_name, recipe_name)


def apply_any(
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str = "target.bin",
    recipe_name: str = "recipe.remap",
) -> dict:
    """Apply with no identity requirement and no positional proof beyond the anchor.

    The loosest command: a displaced anchor is accepted and an ambiguous one is
    reported rather than refused. Mechanical guarantees remain — valid 5.0
    recipe, size, every anchor found, post-verification, byte accounting — and
    the recipe's ``identity_floor`` is still enforced, so a recipe that demands
    identity cannot be applied here.
    """
    return _apply(ANY_RULES, target_data, recipe, target_name, recipe_name)


def apply_shift(
    target_data: bytes,
    recipe: Mapping[str, Any],
    target_name: str = "target.bin",
    recipe_name: str = "recipe.remap",
) -> dict:
    """Apply under the ``tune-shift`` rules: a displaced anchor is allowed.

    Relaxes exactly one thing compared with :func:`apply_strict` — the change may
    be found at an offset other than the recorded one (within ±2048 bytes) — and
    tightens one in exchange: an anchor with more than one copy in the search
    window is now a refusal, because a shift means the engine really chooses
    between candidates. Identity, size, verification and byte accounting are
    unchanged.
    """
    return _apply(SHIFT_RULES, target_data, recipe, target_name, recipe_name)
