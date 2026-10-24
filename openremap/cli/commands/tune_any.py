"""
openremap tune-any <target> <recipe> [--output <out>]

The **loosest** apply command. No identity requirement, a displaced anchor is
accepted, and an anchor with several look-alikes is reported instead of refused
(the "closest to the recorded offset" candidate is written).

What is still enforced: a valid schema-5.0 recipe, the declared size, every
anchor found within ±2048 bytes, post-verification, and byte accounting (nothing
written outside the instructions). The recipe's ``identity_floor`` also still
applies — a recipe that declares it needs firmware identity is refused.

⚠ This command will happily write into a different firmware or ECU. It reports
what the target actually is next to what the recipe was cooked from; read that
line before flashing. There is nothing looser than this.

Exit code 0 = applied, 1 = refused, 2 = bad input.

Examples:
    openremap tune-any other_ecu.bin stage1.remap --output risky.bin
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from openremap.cli.commands.tune_strict import (
    _read_bin,
    _read_recipe,
    emit_result,
)
from openremap.core.services.recipes.apply.engine import apply_any


def tune_any(
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
    """Apply with no identity check at all — identity, shift and ambiguity relaxed."""
    target_data = _read_bin(target, "target")
    recipe_dict = _read_recipe(recipe)
    result = apply_any(target_data, recipe_dict, target.name, recipe.name)
    emit_result(result, target, output, json_output, report_output)
