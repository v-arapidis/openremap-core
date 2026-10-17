"""
Golden-case builder for the synthetic corpus.

Shared by ``test_golden.py`` (which compares against the committed files) and
by ``_dev/corpus/update_golden.py`` (which rewrites them) — one builder, so the
two sides can never disagree about what a golden contains.

A case is a *parameter set*, not a file: the input is regenerated from
``manifest.json`` (seed included), which is what lets this corpus live in the
repository and run in CI.  Nothing here touches real ECU data.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from openremap.core.serialize import to_json_safe
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.cook import cook_recipe
from openremap.core.services.recipes.tune import tune_binary
from openremap.core.synth import generate_dump

CASE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = CASE_ROOT / "manifest.json"
EXPECTED_NAME = "golden.json"

STOCK_NAME = "stock.bin"
TUNED_NAME = "tuned.bin"
RECIPE_NAME = "recipe.remap"


# ---------------------------------------------------------------------------
# Manifest access
# ---------------------------------------------------------------------------


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def case_ids() -> list[str]:
    return [c["id"] for c in load_manifest()["cases"]]


def get_case(case_id: str) -> dict:
    for case in load_manifest()["cases"]:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def expected_path(case_id: str) -> Path:
    return CASE_ROOT / "cases" / case_id / EXPECTED_NAME


def load_expected(case_id: str) -> dict:
    return json.loads(expected_path(case_id).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Building one golden
# ---------------------------------------------------------------------------


def build_case(case: dict) -> dict:
    """Run *case* through the engine and return its golden document."""
    params = dict(case["params"])
    stock = generate_dump(**params)
    tuned = stock.tuned(percent=case["tune_percent"])

    recipe_result = cook_recipe(
        stock.data, tuned.data, STOCK_NAME, TUNED_NAME
    )
    recipe = _normalise_recipe(recipe_result["recipe"])
    applied = tune_binary(stock.data, recipe_result["recipe"], STOCK_NAME, RECIPE_NAME)

    return {
        "case": case["id"],
        "params": params,
        "tune_percent": case["tune_percent"],
        "stock_sha256": stock.sha256,
        "tuned_sha256": tuned.sha256,
        "planted": [_planted(m) for m in stock.maps],
        "identify": to_json_safe(identify_ecu(stock.data, STOCK_NAME)),
        "scan_maps": [_table_summary(t) for t in scan_map_tables(stock.data)],
        "recipe": recipe,
        "repatch": {
            "all_ok": applied["all_ok"],
            "apply_error": applied["apply_error"],
            "target_md5": applied["target_md5"],
            "tuned_md5": applied["tuned_md5"],
            "tuned_sha256": _payload_sha256(applied.get("tuned_b64")),
        },
        "tamper": {
            field: _tamper(stock.data, recipe_result["recipe"], field)
            for field in ("ctx", "ob")
        },
    }


def _planted(m) -> dict:
    """Compact ground truth: where the map is, not what is in it."""
    return {
        "map_id": m.map_id,
        "offset": m.offset,
        "cols": m.cols,
        "rows": m.rows,
        "cell_width": m.cell_width,
        "byte_order": m.byte_order,
    }


def _table_summary(t) -> dict:
    """A detected candidate, with the score rounded for cross-machine stability."""
    return {
        "offset": t.offset,
        "cols": t.cols,
        "rows": t.rows,
        "cell_width": t.cell_width,
        "byte_order": t.byte_order,
        "x_axis_offset": t.x_axis_offset,
        "y_axis_offset": t.y_axis_offset,
        "score": round(float(t.score), 2),
    }


def _normalise_recipe(recipe: dict) -> dict:
    """Copy the recipe with wall-clock fields neutralised.

    ``creator.created_at`` is the one known nondeterministic field; the key is
    kept (as a placeholder) so a schema change is still visible in the diff.
    """
    out = json.loads(json.dumps(recipe))  # detach + JSON-round-trip
    creator = out.get("creator")
    if isinstance(creator, dict) and "created_at" in creator:
        creator["created_at"] = "<normalised:wall-clock>"
    return out


def _payload_sha256(tuned_b64: str | None) -> str | None:
    if not tuned_b64:
        return None
    return hashlib.sha256(base64.b64decode(tuned_b64)).hexdigest()


def _tamper(target: bytes, recipe: dict, field: str) -> dict:
    """Corrupt one instruction field and record how the engine refused.

    ``ctx`` is caught by the patcher (phase 2, with an actionable message),
    ``ob`` by strict validation (phase 1, no message).  Both must refuse and
    write nothing — that all-or-nothing promise is the point of pinning them.
    """
    if not recipe.get("instructions"):
        return {"skipped": "recipe has no instructions"}

    bad = json.loads(json.dumps(recipe))
    instruction = bad["instructions"][0]
    value = instruction.get(field) or ""
    if not value:
        return {"skipped": f"instruction has no {field}"}
    instruction[field] = ("00" if value[:2] != "00" else "ff") + value[2:]

    result = tune_binary(target, bad, STOCK_NAME, RECIPE_NAME)
    return {
        "all_ok": result["all_ok"],
        "apply_error": result["apply_error"],
        "wrote_nothing": result.get("tuned_b64") is None,
    }


# ---------------------------------------------------------------------------
# Comparison — a golden corpus is only useful if a failure says WHAT moved
# ---------------------------------------------------------------------------


def first_difference(expected, actual, path: str = "$") -> str | None:
    """Return a human-readable path to the first difference, or ``None``."""
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float)) and isinstance(actual, (int, float))
    ):
        return f"{path}: type {type(expected).__name__} → {type(actual).__name__}"
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                return f"{path}.{key}: unexpected new key"
            if key not in actual:
                return f"{path}.{key}: key disappeared"
            found = first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: {len(expected)} entries → {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            found = first_difference(e, a, f"{path}[{i}]")
            if found:
                return found
        return None
    if expected != actual:
        return f"{path}: {expected!r} → {actual!r}"
    return None
