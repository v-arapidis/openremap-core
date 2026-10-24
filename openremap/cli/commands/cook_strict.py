"""
openremap cook-strict <stock> <tuned> --output recipe.remap

Build a schema-5.0 recipe from a stock/tuned pair, requiring that **every
anchor is unique** in the stock binary. The result declares
``identity_floor: any`` — a self-verifying recipe that *every* apply command can
use, including the identity-free ones (`tune-unique`, `tune-any`).

What the cook guarantees:

  * both files are the same size and the **same firmware** (refused otherwise),
  * every changed block carries a context anchor that occurs exactly once,
  * nothing is excluded — no VIN, no checksum, no "volatile" bytes. Anything
    suspicious becomes a *flag* on the instruction, which is annotation only.

The recipe is verified against the apply-side reader before it is written: a
producer that emits something its consumer rejects is a bug, and it fails here
instead of confusing someone later.

Exit code 0 = recipe written, 1 = refused, 2 = bad input.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from openremap.cli.commands.tune_strict import _read_bin
from openremap.core.services.recipes.produce.builder import produce_recipe
from openremap.core.services.recipes.produce.modes import COOK_STRICT, CookMode

_W = 62


# ---------------------------------------------------------------------------
# Defaults / presentation
# ---------------------------------------------------------------------------


def _default_output(tuned: Path) -> Path:
    return tuned.parent / f"{tuned.stem}.remap"


def _print_report(report: dict, output: Path | None) -> None:
    typer.echo("")
    typer.echo(typer.style(f"  {report['command']}  ·  {report['files']['stock']}", bold=True))
    typer.echo(typer.style("  " + "─" * _W, fg=typer.colors.BRIGHT_BLACK))
    typer.echo(
        f"  MODE          anchors {'must be unique' if report['mode']['require_unique'] else 'may repeat'}"
        f" · exclusions: {report['mode']['exclusions']}"
    )

    identity = report.get("identity") or {}
    if identity:
        typer.echo(f"  STOCK KEY     {identity.get('stock_match_key')}")
        typer.echo(f"  TUNED KEY     {identity.get('tuned_match_key')}")

    counts = report.get("counts") or {}
    if counts:
        typer.echo(
            f"  CHANGES       {counts.get('instructions', 0)} instructions · "
            f"{counts.get('bytes_changed', 0):,} bytes · {counts.get('maps', 0)} maps"
        )

    regions = report.get("regions") or {}
    if regions.get("tagged"):
        by_region = ", ".join(f"{k} {v}" for k, v in (regions.get("by_region") or {}).items())
        typer.echo(f"  REGIONS       {regions.get('tagged')} tagged · {by_region}")

    flags = report.get("flags") or {}
    if flags:
        summary = ", ".join(f"{kind} {count}" for kind, count in flags.items())
        typer.echo(f"  FLAGS         {summary}")
    else:
        typer.echo("  FLAGS         none")

    for note in report.get("notes") or []:
        typer.echo(typer.style(f"  ⚠ {note}", fg=typer.colors.YELLOW))

    typer.echo("")
    refusal = report.get("refusal")
    if refusal:
        typer.echo(
            typer.style(f"  ✗ REFUSED at {refusal['rule']}", fg=typer.colors.RED, bold=True)
        )
        for line in str(refusal["message"]).splitlines():
            typer.echo(f"    {line}")
        if report.get("next_step"):
            typer.echo(typer.style(f"  → {report['next_step']}", fg=typer.colors.CYAN))
        typer.echo("")
        return

    contract = report.get("contract") or {}
    typer.echo(typer.style("  ✅ RECIPE BUILT", fg=typer.colors.GREEN, bold=True))
    typer.echo(f"  identity_floor {contract.get('identity_floor')}")
    typer.echo(f"  sections       {', '.join(contract.get('sections') or []) or '—'}")
    if output:
        typer.echo(typer.style(f"  saved to       {output}", fg=typer.colors.CYAN))
    typer.echo("")


# ---------------------------------------------------------------------------
# Shared command body
# ---------------------------------------------------------------------------


def emit_result(
    result: dict,
    tuned: Path,
    output: Path | None,
    json_output: bool,
    report_output: Path | None,
) -> None:
    """Write the recipe/report, print, and exit non-zero when refused."""
    report = result["report"]

    resolved_output: Path | None = None
    if result["recipe"] is not None:
        resolved_output = output or _default_output(tuned)
        try:
            resolved_output.write_text(
                json.dumps(result["recipe"], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            typer.echo(
                typer.style(f"Error writing recipe: {exc}", fg=typer.colors.RED, bold=True),
                err=True,
            )
            raise typer.Exit(code=2)

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

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "produced": result["recipe"] is not None,
                    "output": str(resolved_output) if resolved_output else None,
                    "report": report,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        _print_report(report, resolved_output)

    if result["recipe"] is None:
        raise typer.Exit(code=1)


def run_cook(
    mode: CookMode,
    stock: Path,
    tuned: Path,
    output: Path | None,
    json_output: bool,
    report_output: Path | None,
) -> None:
    stock_data = _read_bin(stock, "stock")
    tuned_data = _read_bin(tuned, "tuned")
    result = produce_recipe(stock_data, tuned_data, stock.name, tuned.name, mode=mode)
    emit_result(result, tuned, output, json_output, report_output)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def cook_strict(
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
    """Build a 5.0 recipe requiring unique anchors (usable by every apply command)."""
    run_cook(COOK_STRICT, stock, tuned, output, json_output, report_output)
