"""
openremap tune-shift <target> <recipe> [--output <out>]

Apply a recipe when the target is the **same firmware** but the change is not at
the recorded offset. This is one rung looser than ``tune-strict`` and relaxes
exactly one thing:

  strict   every original byte must be at its recorded offset, shift == 0
  shift    the anchor may be found up to ±2048 bytes away; the shift is reported

In exchange it tightens one thing: an anchor with more than one copy in the
search window is a refusal. Once a shift is allowed the engine really does
choose between candidates, so "closest match" is no longer safe.

Everything else is unchanged: schema-5.0 recipe, identity floor, same firmware
(match_key), size, post-verification, and byte accounting (measured against the
offsets actually written).

Reported but never decisive: the number of anchor look-alikes, flash-region split.

The original file is never modified. Exit code 0 = applied, 1 = refused,
2 = bad input.

Examples:
    openremap tune-shift target.bin stage1.remap
    openremap tune-shift target.bin stage1.remap --output car2_tuned.bin
    openremap tune-shift target.bin stage1.remap --json
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated

import typer

from openremap.cli.commands.tune_strict import (
    _default_output,
    _print_report,
    _read_bin,
    _read_recipe,
)
from openremap.core.services.recipes.apply.engine import apply_shift


def tune_shift(
    target: Annotated[Path, typer.Argument(help="Path to the target ECU binary.")],
    recipe: Annotated[Path, typer.Argument(help="Path to the .remap recipe.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Where to write the patched binary (default: <target>_tuned.bin).",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the structured report as JSON."),
    ] = False,
    report_output: Annotated[
        Path | None,
        typer.Option("--report", "-r", help="Also write the JSON report to this path."),
    ] = None,
) -> None:
    """Apply a recipe allowing the change to be found at a displaced offset."""
    target_data = _read_bin(target, "target")
    recipe_dict = _read_recipe(recipe)

    result = apply_shift(target_data, recipe_dict, target.name, recipe.name)
    report = result["report"]

    if report_output:
        try:
            report_output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            typer.echo(
                typer.style(f"Error writing report: {exc}", fg=typer.colors.RED, bold=True),
                err=True,
            )
            raise typer.Exit(code=2)

    resolved_output: Path | None = None
    if result["applied"]:
        resolved_output = output or _default_output(target)
        try:
            resolved_output.write_bytes(base64.b64decode(result["patched_b64"]))
        except OSError as exc:
            typer.echo(
                typer.style(f"Error writing output: {exc}", fg=typer.colors.RED, bold=True),
                err=True,
            )
            raise typer.Exit(code=2)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "applied": result["applied"],
                    "patched_sha256": result["patched_sha256"],
                    "output": str(resolved_output) if resolved_output else None,
                    "report": report,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        _print_report(result, target, resolved_output)

    if not result["applied"]:
        raise typer.Exit(code=1)
