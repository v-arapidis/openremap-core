"""
openremap tune-unique <target> <recipe> [--output <out>]

Apply a recipe to a target that **cannot be identified**, as long as the proof of
position holds twice over:

  * every original byte is exactly at its recorded offset (``shift == 0``), and
  * each anchor occurs exactly once in the search window.

Those are two independent proofs of *where* the write lands, which is why the
firmware label is not needed here. This is the command for dumps that the
identity-gated commands must refuse — an obscure ECU, or a dump whose ident
region was modified.

It is stricter than ``tune-any`` in both places that matter: it does not accept a
displaced anchor, and it refuses an ambiguous one.

One thing it cannot loosen: the recipe's own ``identity_floor``. A recipe that
declares it needs firmware identity is refused here — cook it with
``identity_floor: any`` for a recipe meant for unidentified targets.

Exit code 0 = applied, 1 = refused, 2 = bad input.

Examples:
    openremap tune-unique unknown_dump.bin stage1.remap
    openremap tune-unique unknown_dump.bin stage1.remap --output tuned.bin
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
from openremap.core.services.recipes.apply.engine import apply_unique


def tune_unique(
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
    """Apply without identity, but with exact offsets and unique anchors."""
    target_data = _read_bin(target, "target")
    recipe_dict = _read_recipe(recipe)
    result = apply_unique(target_data, recipe_dict, target.name, recipe.name)
    emit_result(result, target, output, json_output, report_output)
