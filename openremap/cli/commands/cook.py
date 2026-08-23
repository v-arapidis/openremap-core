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

from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED = (".bin", ".ori", ".hex")


def _check_bin(path: Path, label: str) -> None:
    """Validate that a path points to an allowed, non-empty binary file."""
    if path.suffix.lower() not in _ALLOWED:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, or .hex file.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)


def _read_bin(path: Path, label: str) -> bytes:
    """Read binary file contents with user-friendly error handling."""
    _check_bin(path, label)
    try:
        data = path.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error reading {label} file: {exc}", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if not data:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' is empty.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    return data


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

        if annotate_maps:
            from openremap.core.services.recipes.recipe_maps import attach_maps

            attach_maps(recipe, original_data)
            map_count = len(recipe["maps"])
            typer.echo(
                typer.style(
                    f"\n  🗺  Annotated {map_count} calibration map(s) — "
                    f"recipe schema bumped to 4.4.",
                    fg=typer.colors.CYAN,
                ),
                err=True,
            )
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
            typer.echo(
                typer.style(
                    f"\n  ⚠  {len(non_unique_warnings)} instruction(s) have non-unique "
                    "anchors — this recipe is only reliable on THIS exact binary. "
                    "Applying it to a different software revision may patch the "
                    "wrong location.",
                    fg=typer.colors.YELLOW,
                    bold=True,
                ),
                err=True,
            )

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
