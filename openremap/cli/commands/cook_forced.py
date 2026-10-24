"""
openremap cook-forced <stock> <tuned> --output recipe.remap

Build a schema-5.0 recipe **accepting non-unique anchors** — the escape hatch for
a change that lands inside blank flash or padding, where a ~500-byte context
cannot occur only once.

The result declares ``identity_floor: firmware``: only the apply commands that
verify the target's ``match_key`` may use it (`tune-strict`, `tune-shift`). The
identity-free commands (`tune-unique`, `tune-any`) will refuse it — deliberately,
because with a repeated anchor the firmware label is the only thing that can
vouch for the position.

Everything else is identical to `cook-strict`: same guards (size, one firmware),
nothing excluded, and the recipe is verified against the apply-side reader.

Exit code 0 = recipe written, 1 = refused, 2 = bad input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from openremap.cli.commands.cook_strict import run_cook
from openremap.core.services.recipes.produce.modes import COOK_FORCED


def cook_forced(
    stock: Annotated[Path, typer.Argument(help="The unmodified (stock) binary.")],
    tuned: Annotated[Path, typer.Argument(help="The tuned binary.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Where to write the .remap recipe."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the structured report as JSON.")
    ] = False,
    report_output: Annotated[
        Path | None,
        typer.Option("--report", "-r", help="Also write the JSON report to this path."),
    ] = None,
) -> None:
    """Build a 5.0 recipe accepting non-unique anchors (same-firmware apply only)."""
    run_cook(COOK_FORCED, stock, tuned, output, json_output, report_output)
