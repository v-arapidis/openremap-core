"""
openremap merge <A.remap> <B.remap> [--stock original.bin] [-o merged.remap]

Combine two recipes built from the same family of originals into one —
like git's three-way merge: the stock binary is the common ancestor.

Examples:
    openremap merge egr_off.remap stage1.remap --stock stock.bin -o both.remap
    openremap merge a.remap b.remap --stock stock.bin --strict
"""

from __future__ import annotations

import json
import orjson
from pathlib import Path

import typer

from openremap.core.cli.io import load_binary_file
from openremap.core.services.recipes.recipe_merge import MergeConflict, merge_recipes

_ALLOWED = (".remap", ".json", ".openremap")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_recipe(path: Path, label: str) -> dict:
    """Load a recipe dict; exit 1 on unreadable/malformed files."""
    try:
        data = orjson.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(
            typer.style(
                f"Error: cannot read {label} recipe '{path.name}': {exc}",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    if not isinstance(data, dict) or "instructions" not in data:
        typer.echo(
            typer.style(
                f"Error: '{path.name}' is not a valid .remap recipe "
                f"(no 'instructions' field).",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    return data


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def merge(
    recipe_a: Path = typer.Argument(
        ...,
        help="First recipe (.remap).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    recipe_b: Path = typer.Argument(
        ...,
        help="Second recipe (.remap).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    stock: Path | None = typer.Option(
        None,
        "--stock",
        help=(
            "The common stock (original) binary. Every instruction from "
            "both recipes is validated against it; mismatched instructions "
            "are skipped with a report (--strict aborts instead). Without "
            "--stock, both recipes must declare identical ecu.sha256."
        ),
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the merged recipe to this file instead of stdout.",
        writable=True,
        resolve_path=True,
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help=(
            "Abort the merge instead of skipping instructions that do not "
            "match the stock binary."
        ),
    ),
) -> None:
    """
    Merge two recipes into one, validated against a common stock binary.

    Merge rules:
      - same offset, same values  → one copy kept
      - same offset, other values → conflict (same address, different edit)
      - overlapping ranges         → conflict (different edit boundaries)
      - different offsets          → both kept

    The stock binary is the merge base: instructions that don't match it
    (recipes built from slightly different originals — e.g. VIN area)
    are reported and skipped.  The merged recipe re-checks anchor
    uniqueness and re-annotates maps from the stock.
    """
    data_a = _load_recipe(recipe_a, "first")
    data_b = _load_recipe(recipe_b, "second")

    stock_data: bytes | None = None
    if stock is not None:
        stock_data, _fmt = load_binary_file(stock, "Stock")

    try:
        merged = merge_recipes(
            data_a,
            data_b,
            name_a=recipe_a.name,
            name_b=recipe_b.name,
            stock_data=stock_data,
            strict=strict,
        )
    except MergeConflict as exc:
        typer.echo(
            typer.style(
                f"\n  ✗ Merge conflict:\n  {exc}\n\n"
                "  Fix the conflicting edits by hand (e.g. keep one recipe's "
                "value), then re-run the merge.",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    n_a = len(data_a.get("instructions", []))
    n_b = len(data_b.get("instructions", []))
    n_m = len(merged["instructions"])
    warnings = merged.get("ecu", {}).get("cook_warnings", [])

    typer.echo(
        f"\n  ✅ Merged {recipe_a.name} ({n_a} instructions) + "
        f"{recipe_b.name} ({n_b} instructions) → {n_m} instructions."
    )
    if warnings:
        typer.echo("")
        for w in warnings:
            typer.echo(
                typer.style(f"  ⚠  {w}", fg=typer.colors.YELLOW),
                err=True,
            )

    json_content = json.dumps(
        merged, indent=2, ensure_ascii=False, sort_keys=True
    )

    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json_content, encoding="utf-8")
        except OSError as exc:
            typer.echo(
                typer.style(
                    f"Error: could not write '{output}': {exc}",
                    fg=typer.colors.RED, bold=True,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(
            typer.style(
                f"\n  Saved merged recipe to {output} "
                f"(schema {merged['schema_version']})",
                fg=typer.colors.GREEN,
            ),
        )
    else:
        typer.echo(json_content)
