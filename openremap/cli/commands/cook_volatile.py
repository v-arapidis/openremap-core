"""
openremap cook-volatile <original> <modified> --output portable.remap

Cook a car-portable recipe by diffing an original and a modified ECU
binary, detecting volatile / vehicle-specific instructions (VIN records,
checksum stores, serial/IMMO) and excluding the near-certain ones from
the patch list.

A recipe cooked from (stockA, tunedA) fails on stockB of the same SW
revision whenever stockA and stockB differ inside an instruction's
anchor window — in practice when the tune touched volatile bytes (VIN in
flash, checksum store bytes recomputed on save, serial/IMMO counters).
``cook-volatile`` classifies each instruction at cook time, excludes the
near-certain volatile classes (VIN + verified checksum stores), and
records everything in a new ``volatile`` recipe section with evidence.

Policy:
  - Separate command — ``cook`` stays byte-identical (no new flags).
  - No interactive prompts — deterministic and scriptable.
  - Exclusion only for near-certain classes; uncertain classes degrade
    to flags/warnings, never silent drops.
  - The excluded set + evidence is recorded in the recipe — auditable
    and reviewable in git.

Examples:
    openremap cook-volatile stock.bin stage1.bin --output portable.remap
    openremap cook-volatile stock.bin stage1.bin --no-exclude
    openremap cook-volatile stock.bin stage1.bin --exclude-uncertain
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer

from openremap.cli.commands.cook import _print_summary, _read_bin
from openremap.core.services.recipes.recipe_builder import (
    ECUDiffAnalyzer,
    compute_fingerprint,
)
from openremap.core.services.recipes.recipe_maps import attach_maps
from openremap.core.services.recipes.volatile import (
    VolatileFinding,
    classify_volatile,
)


# ---------------------------------------------------------------------------
# Statistics — recomputed after filtering (counts + bytes of the kept set)
# ---------------------------------------------------------------------------


def _recompute_stats(
    instructions: list,
    file_size: int,
    context_size: int,
    max_context_size: int,
) -> dict:
    """Statistical summary of the KEPT instruction set."""
    if not instructions:
        return {}

    total_changed = sum(inst["size"] for inst in instructions)
    single = sum(1 for inst in instructions if inst["size"] == 1)

    return {
        "total_changes": len(instructions),
        "total_bytes_changed": total_changed,
        "percentage_changed": round(total_changed / file_size * 100, 4),
        "single_byte_changes": single,
        "multi_byte_changes": len(instructions) - single,
        "largest_change_size": max(inst["size"] for inst in instructions),
        "smallest_change_size": min(inst["size"] for inst in instructions),
        "min_context_size": context_size,
        "max_context_size": max_context_size,
    }


# ---------------------------------------------------------------------------
# Review summary printing
# ---------------------------------------------------------------------------


def _print_review_lines(
    findings: list[VolatileFinding],
    heading: str,
) -> None:
    """Print one line per finding — the per-instruction review list."""
    if not findings:
        return
    typer.echo(
        typer.style(f"\n  {heading}", fg=typer.colors.YELLOW, bold=True),
        err=True,
    )
    for f in findings:
        first, *rest = f.evidence
        typer.echo(
            typer.style(
                f"     0x{f.offset:X} — {f.kind} ({f.confidence}): {first}",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )
        for line in rest:
            typer.echo(typer.style(f"         {line}", fg=typer.colors.YELLOW), err=True)


def _print_volatile_summary(excluded: list, flagged: list, no_exclude: bool) -> None:
    """Print the volatile classification summary (always shown)."""
    excluded_bytes = sum(f.size for f in excluded)
    typer.echo("")
    typer.echo(
        typer.style(
            f"  Volatile summary: {len(excluded):,} excluded "
            f"({excluded_bytes:,} bytes), {len(flagged):,} flagged",
            fg=typer.colors.CYAN,
            bold=True,
        )
    )
    if no_exclude:
        typer.echo(
            typer.style(
                "\n  ⚠  --no-exclude: nothing was removed.  Volatile instructions "
                "stay in the patch list, so this recipe is only reliable on THIS "
                "exact binary — applying it to a different car may patch the "
                "wrong location or fail.",
                fg=typer.colors.YELLOW,
                bold=True,
            ),
            err=True,
        )


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def cook_volatile(
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
            "in the stock binary (cook parity — same semantics)."
        ),
    ),
    no_exclude: bool = typer.Option(
        False,
        "--no-exclude",
        help=(
            "Do NOT exclude anything — volatile instructions stay in the "
            "patch list, only recorded in the volatile section as flagged "
            "(max safety, zero portability)."
        ),
    ),
    exclude_uncertain: bool = typer.Option(
        False,
        "--exclude-uncertain",
        help=(
            "Additionally exclude warning-class instructions (ident-block "
            "strings, low-entropy counter clusters) — opt-in, recorded in "
            "the recipe as lower-confidence exclusions."
        ),
    ),
    accept_volatile: bool = typer.Option(
        False,
        "--accept-volatile",
        help=(
            "Suppress the per-instruction review list (summary only) — "
            "mirror of --allow-non-unique semantics."
        ),
    ),
    annotate_maps: bool = typer.Option(
        True,
        "--annotate-maps/--no-annotate-maps",
        help=(
            "Annotate the recipe with a 'maps' section: scan the stock "
            "binary for calibration tables and record which kept map each "
            "instruction touches.  Runs AFTER volatile filtering, so "
            "instruction_refs index the kept set.  On by default; "
            "--no-annotate-maps emits the lean format."
        ),
    ),
    pretty: bool = typer.Option(
        True,
        "--pretty/--compact",
        help="Pretty-print JSON output with indentation (default: pretty).",
    ),
) -> None:
    """
    Cook a car-portable recipe by diffing an original and a modified ECU binary.

    Identical pipeline to ``cook`` (byte diff, context anchors, guards),
    plus a volatile-classification pass: near-certain volatile
    instructions (VIN records, verified checksum stores) are detected and
    EXCLUDED from the patch list, with evidence recorded in a new
    ``volatile`` recipe section (schema 4.5).  Warning-class instructions
    (ident-block strings, low-entropy counters) are flagged only — exclude
    them too with --exclude-uncertain.  Use --no-exclude to keep every
    instruction (max safety, zero portability).
    """
    original_data = _read_bin(original, "Original")
    modified_data = _read_bin(modified, "Modified")

    typer.echo(
        f"\n  Cooking portable recipe from "
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

        report = classify_volatile(
            recipe,
            original_data,
            exclude_uncertain=exclude_uncertain,
        )

        # --- Filter: drop near-certain volatile instructions ---------------
        if not no_exclude:
            excluded_indices = {f.index for f in report.excluded}
            recipe["instructions"] = [
                inst
                for i, inst in enumerate(recipe["instructions"])
                if i not in excluded_indices
            ]

        # Recompute everything derived from the instruction set.
        recipe["statistics"] = _recompute_stats(
            recipe["instructions"],
            len(original_data),
            analyzer.context_size,
            analyzer.max_context_size,
        )
        recipe["fingerprint"] = compute_fingerprint(recipe["instructions"])
        recipe["metadata"]["instruction_count"] = len(recipe["instructions"])

        # --- volatile section (schema 4.5) ---------------------------------
        if no_exclude:
            # Nothing was removed: record every finding as flagged so the
            # recipe stays self-consistent (excluded must mean "removed").
            excluded_findings: list[VolatileFinding] = []
            flagged_findings: list[VolatileFinding] = [
                replace(f, action="flagged")
                for f in (*report.excluded, *report.flagged)
            ]
        else:
            excluded_findings = list(report.excluded)
            flagged_findings = list(report.flagged)

        volatile_summary = {
            "excluded_count": len(excluded_findings),
            "flagged_count": len(flagged_findings),
            "bytes_excluded": sum(f.size for f in excluded_findings),
        }
        recipe["volatile"] = {
            "excluded": [f.to_dict() for f in excluded_findings],
            "flagged": [f.to_dict() for f in flagged_findings],
            "summary": volatile_summary,
        }
        recipe["metadata"]["source"] = "cook_volatile"
        recipe["metadata"]["volatile"] = volatile_summary
        recipe["metadata"]["excluded_volatile"] = not no_exclude

        if annotate_maps:
            # AFTER filtering — maps[].instruction_refs index the KEPT set.
            attach_maps(recipe, original_data)
            map_count = len(recipe["maps"])
            typer.echo(
                typer.style(
                    f"\n  🗺  Annotated {map_count} calibration map(s) — "
                    f"recipe schema bumped to 4.5.",
                    fg=typer.colors.CYAN,
                ),
                err=True,
            )

        # attach_maps bumps schema to 4.4 (MAPS_SCHEMA_VERSION) — set the
        # volatile schema level AFTER it, so 4.5 always wins.
        recipe["schema_version"] = "4.5"
    except Exception as exc:
        msg = str(exc)
        if "non-unique context" in msg:
            typer.echo(
                typer.style(
                    f"\n  Error: cook-volatile failed — {msg}",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
            typer.echo(
                typer.style(
                    "\n  Re-run with --allow-non-unique to produce the recipe "
                    "with warnings — only if it will be applied to this exact "
                    "binary.",
                    fg=typer.colors.YELLOW,
                ),
                err=True,
            )
        else:
            typer.echo(
                typer.style(
                    f"\n  Error: cook-volatile failed — {msg}",
                    fg=typer.colors.RED,
                    bold=True,
                ),
                err=True,
            )
        raise typer.Exit(code=1)

    # --- Review output -----------------------------------------------------
    if not accept_volatile:
        _print_review_lines(
            excluded_findings,
            f"{len(excluded_findings):,} instruction(s) excluded as volatile:",
        )
        _print_review_lines(
            flagged_findings,
            f"{len(flagged_findings):,} instruction(s) flagged for review:",
        )
    _print_volatile_summary(excluded_findings, flagged_findings, no_exclude)

    indent = 2 if pretty else None
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
