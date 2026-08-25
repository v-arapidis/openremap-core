"""
openremap cook <original> <modified> --output recipe.remap

Cook a recipe by diffing an original and a modified ECU binary.

Examples:
    openremap cook stock.bin stage1.bin --output recipe.remap
    openremap cook stock.bin stage1.bin --output recipe.remap --pretty
    openremap cook stock.bin stage1.bin --context-size 64 --output recipe.remap
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from openremap.cli.io import load_binary_file
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.recipe_regions import tag_instruction_regions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED = (".bin", ".ori", ".hex", ".s19", ".srec", ".mot")


def _check_bin(path: Path, label: str) -> None:
    """Validate that a path points to an allowed, non-empty binary file."""
    if path.suffix.lower() not in _ALLOWED:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, .hex, .s19, .srec, or .mot file.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)


def _read_bin(path: Path, label: str) -> bytes:
    """Read + decode a binary file (raw, Intel HEX, or S-Record)."""
    _check_bin(path, label)
    data, _fmt = load_binary_file(path, label)
    return data


def _tag_regions(recipe: dict, original_data: bytes, tables: list | None = None) -> dict:
    """Advisory flash-layout region tags for recipe instructions.

    Never blocks and never filters: any failure silently degrades to an
    empty summary so region tagging can never fail a cook.
    """
    try:
        return tag_instruction_regions(recipe, original_data, tables)
    except Exception:
        return {"tagged": 0, "risky": 0, "by_region": {}}


def _print_region_warning(recipe: dict, summary: dict) -> None:
    """Print the advisory code/erased-area portability warning (if any)."""
    if not summary.get("risky"):
        return
    risky_offsets: list[str] = []
    for inst in recipe.get("instructions", []):
        if any(f.get("kind") == "CODE_AREA" for f in inst.get("flags", [])):
            risky_offsets.append(
                f"0x{inst.get('offset_hex', format(inst['offset'], 'X'))}"
            )
    preview = ", ".join(risky_offsets[:6])
    if len(risky_offsets) > 6:
        preview += f" … and {len(risky_offsets) - 6} more"
    typer.echo(
        typer.style(
            f"\n  ⚠  {summary['risky']} instruction(s) outside the calibration "
            f"region (code/erased/mixed flash area): {preview}\n"
            "     These edits may not apply to other revisions of this ECU. "
            "Region labels are structural estimates.",
            fg=typer.colors.YELLOW,
            bold=True,
        ),
        err=True,
    )


def _print_summary(recipe: dict, output: Optional[Path]) -> None:
    """Print a compact summary of the produced recipe to stdout."""
    ecu = recipe.get("ecu", {})
    stats = recipe.get("statistics", {})
    meta = recipe.get("metadata", {})

    family = ecu.get("ecu_family") or "Unknown"
    manufacturer = ecu.get("manufacturer") or "Unknown"
    match_key = ecu.get("match_key") or "n/a"
    total_changes = stats.get("total_changes", 0)
    total_bytes = stats.get("total_bytes_changed", 0)
    fmt_version = recipe.get("schema_version", "?")

    typer.echo("")
    typer.echo(
        typer.style("  ✅ Recipe built successfully", fg=typer.colors.GREEN, bold=True)
    )
    typer.echo("")

    col = 22
    rows = [
        ("ECU", f"{manufacturer} · {family}"),
        ("Match Key", match_key),
        ("Format Version", fmt_version),
        ("Instructions", f"{total_changes:,}"),
        ("Bytes Changed", f"{total_bytes:,}"),
        ("Original", meta.get("original_file", "?")),
        ("Modified", meta.get("modified_file", "?")),
    ]

    # --- Flagged instructions ---
    flagged = sum(1 for inst in recipe.get("instructions", []) if inst.get("flags"))
    if flagged:
        rows.append(("⚠ Flagged", f"{flagged:,} instruction(s) need review"))

    for label, value in rows:
        typer.echo(f"  {label:<{col}} {value}")

    typer.echo("")
    if output:
        typer.echo(
            f"  Recipe saved to {typer.style(str(output), fg=typer.colors.CYAN, bold=True)}"
        )
    typer.echo("")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def cook(
    original: Path = typer.Argument(
        ...,
        help="The unmodified (stock) ECU binary (.bin, .ori, or .hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    modified: Path = typer.Argument(
        ...,
        help="The tuned ECU binary (.bin, .ori, or .hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "File path to write the recipe to (use .remap extension). "
            "If omitted, the recipe is printed to stdout."
        ),
        writable=True,
        resolve_path=True,
    ),
    context_size: int = typer.Option(
        32,
        "--context-size",
        "-c",
        help="Number of bytes of context to capture before each changed block (default: 32).",
        min=8,
        max=128,
    ),
    allow_non_unique: bool = typer.Option(
        False,
        "--allow-non-unique",
        help=(
            "Allow instructions whose context anchor appears more than once "
            "in the stock binary.  The recipe is produced with warnings, but "
            "applying it to a DIFFERENT software revision becomes unreliable "
            "— only use for same-binary recipes."
        ),
    ),
    annotate_maps: bool = typer.Option(
        True,
        "--annotate-maps/--no-annotate-maps",
        help=(
            "Annotate the recipe with a 'maps' section (schema 4.4): scan "
            "the stock binary for calibration tables and record which map "
            "each instruction touches, with probabilistic labels (fuel, "
            "timing, boost, …).  Makes the recipe human-reviewable in git. "
            "On by default; --no-annotate-maps emits the lean 4.3 format "
            "(no map scan, no maps section)."
        ),
    ),
    pretty: bool = typer.Option(
        True,
        "--pretty/--compact",
        help="Pretty-print JSON output with indentation (default: pretty).",
    ),
) -> None:
    """
    Cook a recipe by diffing an original and a modified ECU binary.

    The recipe captures every changed byte block along with its offset,
    original bytes (ob), modified bytes (mb), and a context anchor (ctx)
    used during patching. The ECU identity block is derived automatically
    from the original binary.

    By default the cook ABORTS when a changed block's context anchor is not
    unique in the stock binary — such recipes cannot be applied reliably to
    other software revisions.  Pass --allow-non-unique to produce the recipe
    anyway (with warnings), accepting same-binary-only reliability.

    Save the output recipe — it is the input for all validate and patch commands.
    """
    original_data = _read_bin(original, "Original")
    modified_data = _read_bin(modified, "Modified")

    typer.echo(
        f"\n  Cooking recipe from "
        f"{typer.style(original.name, fg=typer.colors.CYAN)} vs "
        f"{typer.style(modified.name, fg=typer.colors.CYAN)} …"
    )

    try:
        analyzer = ECUDiffAnalyzer(
            original_data=original_data,
            modified_data=modified_data,
            original_filename=original.name,
            modified_filename=modified.name,
            context_size=context_size,
            require_unique=not allow_non_unique,
        )
        recipe = analyzer.build_recipe()

        # One structural scan, shared by map annotation and region tags —
        # never scan the stock binary twice.
        shared_tables = scan_map_tables(
            original_data, min_score=0.55, max_series_tables=16,
        )

        if annotate_maps:
            from openremap.core.services.recipes.recipe_maps import attach_maps

            attach_maps(recipe, original_data, tables=shared_tables)
            map_count = len(recipe["maps"])
            typer.echo(
                typer.style(
                    f"\n  🗺  Annotated {map_count} calibration map(s) — "
                    f"recipe schema bumped to 4.4.",
                    fg=typer.colors.CYAN,
                ),
                err=True,
            )

        # Advisory flash-layout region tags (never blocks, never filters).
        region_summary = _tag_regions(recipe, original_data, shared_tables)
    except Exception as exc:
        # Includes the size-mismatch hard error from the pre-cook guard and
        # the Guard-3 non-unique-anchor abort.
        msg = str(exc)
        if "non-unique context" in msg:
            typer.echo(
                typer.style(
                    f"\n  Error: cook failed — {msg}", fg=typer.colors.RED, bold=True
                ),
                err=True,
            )
            typer.echo(
                typer.style(
                    "\n  These context anchors appear multiple times in the stock "
                    "binary (typically zero-padded or constant regions), so the "
                    "recipe could patch the WRONG location when applied to a "
                    "different software revision.",
                    fg=typer.colors.YELLOW,
                ),
                err=True,
            )
            typer.echo(
                typer.style(
                    "  Re-run with --allow-non-unique to produce the recipe with "
                    "warnings — only if it will be applied to this exact binary.",
                    fg=typer.colors.YELLOW,
                ),
                err=True,
            )
        else:
            typer.echo(
                typer.style(
                    f"\n  Error: cook failed — {msg}", fg=typer.colors.RED, bold=True
                ),
                err=True,
            )
        raise typer.Exit(code=1)

    # Surface any non-fatal warnings produced during recipe building
    # (e.g. ECU identity mismatch between original and modified).
    for warning in analyzer.cook_warnings():
        typer.echo(
            typer.style(
                f"\n  ⚠  Warning: {warning}",
                fg=typer.colors.YELLOW,
                bold=True,
            ),
            err=True,
        )

    if allow_non_unique:
        non_unique_warnings = [
            w for w in analyzer.cook_warnings() if "non-unique" in w
        ]
        if non_unique_warnings:
            # Machine-readable stamp: this recipe is only safe on the exact
            # binary it was cooked from.  tune/validate enforce it via the
            # recipe's ecu.sha256 (hard refusal on any other file, unless
            # the user passes --force).
            recipe.setdefault("metadata", {})["portability"] = "same_file_only"
            typer.echo(
                typer.style(
                    f"\n  ⚠  {len(non_unique_warnings)} instruction(s) have non-unique "
                    "anchors — this recipe is stamped SAME-FILE-ONLY: it will be "
                    "rejected unless applied to the exact binary it was cooked from "
                    "(or --force).",
                    fg=typer.colors.YELLOW,
                    bold=True,
                ),
                err=True,
            )

    # Advisory code/erased-area portability warning (region tags are
    # structural estimates — informational only).
    _print_region_warning(recipe, region_summary)

    # Surface flagged instructions (VIN, checksum suspects, etc.)
    flagged_instructions = [
        inst for inst in recipe.get("instructions", []) if inst.get("flags")
    ]
    if flagged_instructions:
        typer.echo(
            typer.style(
                f"\n  ⚠  {len(flagged_instructions)} instruction(s) flagged for review:",
                fg=typer.colors.YELLOW,
                bold=True,
            ),
            err=True,
        )
        for inst in flagged_instructions:
            offset_hex = inst.get("offset_hex", f"{inst['offset']:X}")
            for flag in inst.get("flags", []):
                typer.echo(
                    typer.style(
                        f"     0x{offset_hex} — {flag['kind']} ({flag['confidence']}): {flag['reason']}",
                        fg=typer.colors.YELLOW,
                    ),
                    err=True,
                )

    indent = 2 if pretty else None
    # Deterministic key order — key order in JSON is presentation-only
    # (JSON objects are unordered maps; every consumer parses via json.load),
    # so sorting keys is purely cosmetic but makes the file layout stable
    # across cooks and schema evolution — re-cooking the same pair produces
    # an identical file except for creator.created_at.
    json_content = json.dumps(
        recipe, indent=indent, ensure_ascii=False, sort_keys=True
    )

    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json_content, encoding="utf-8")
        except OSError as exc:
            typer.echo(
                typer.style(
                    f"\n  Error: could not write recipe to '{output}': {exc}",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        typer.echo(json_content)

    _print_summary(recipe, output)
