"""
openremap audit <STOCK> <TUNED> <RECIPE>

The receipt check: verify that a stock binary, a tuned binary, and a
.remap recipe actually belong together.

Examples:
    openremap audit stock.bin stage1.bin stage1.remap
    openremap audit stock.bin stage1.bin stage1.remap --json
"""

from __future__ import annotations

import json
import orjson
from pathlib import Path

import typer

from openremap.api.commands.audit import audit_binary
from openremap.core.cli.io import load_binary_file


def _read_bin(path: Path, label: str) -> bytes:
    """Read + decode a binary file (raw, Intel HEX, or S-Record)."""
    data, _fmt = load_binary_file(path, label)
    return data


def audit_cmd(
    stock: Path = typer.Argument(
        ...,
        help="The stock (original) ECU binary.",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        resolve_path=True,
    ),
    tuned: Path = typer.Argument(
        ...,
        help="The tuned (modified) ECU binary to check.",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        resolve_path=True,
    ),
    recipe: Path = typer.Argument(
        ...,
        help="The .remap recipe claimed to describe the tune.",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a human-readable report.",
    ),
) -> None:
    """
    Audit a tune: do the stock binary, tuned binary, and recipe belong
    together?

    Three verdicts:
      1. Provenance   — was the recipe built from THIS stock?
      2. Fingerprint  — is the recipe the honest record of the pair?
      3. Unaccounted  — which changed bytes does the recipe NOT explain?

    Not a safety verdict: applicability to another software revision is
    checked by `validate before`, not here.
    """
    stock_data = _read_bin(stock, "stock")
    tuned_data = _read_bin(tuned, "tuned")

    try:
        recipe_data = orjson.loads(recipe.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(
            typer.style(
                f"Error: cannot read recipe '{recipe.name}': {exc}",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    if not isinstance(recipe_data, dict) or "instructions" not in recipe_data:
        typer.echo(
            typer.style(
                f"Error: '{recipe.name}' is not a valid .remap recipe.",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        result = audit_binary(
            stock_data,
            tuned_data,
            recipe_data,
            stock_name=stock.name,
            tuned_name=tuned.name,
            recipe_name=recipe.name,
        )
    except ValueError as exc:
        typer.echo(
            typer.style(
                f"\n  ✗ Audit failed: {exc}", fg=typer.colors.RED, bold=True
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    if as_json:
        out = {
            "stock": str(stock),
            "tuned": str(tuned),
            "recipe": str(recipe),
            **result,
        }
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
        return

    ok = typer.style("✓ PASS", fg=typer.colors.GREEN, bold=True)
    bad = typer.style("✗ FAIL", fg=typer.colors.RED, bold=True)

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Tune Audit", bold=True))
    typer.echo(
        typer.style(
            f"  {stock.name} · {tuned.name} · {recipe.name}", dim=True
        ),
    )
    typer.echo("")

    provenance_ok = result["provenance"]["ok"]
    fingerprint_ok = result["fingerprint"]["ok"]
    unaccounted_blocks = result["unaccounted"]["blocks"]
    unaccounted_bytes = result["unaccounted"]["bytes"]

    typer.echo(
        f"  {ok if provenance_ok else bad}  Provenance — recipe built "
        f"from this stock "
        f"(sha256 {'match' if provenance_ok else 'MISMATCH'})"
    )
    typer.echo(
        f"  {ok if fingerprint_ok else bad}  Fingerprint — recipe "
        f"honestly describes the pair "
        f"({'match' if fingerprint_ok else 'MISMATCH'})"
    )

    if unaccounted_blocks:
        typer.echo(
            f"  {bad}  Unaccounted — {unaccounted_bytes} byte(s) in "
            f"{len(unaccounted_blocks)} block(s) changed but NOT in "
            f"the recipe:"
        )
        for b in unaccounted_blocks:
            typer.echo(
                typer.style(
                    f"     0x{b['offset']:08X}  {b['size']:>6} bytes  "
                    f"[{b['region']}]",
                    fg=typer.colors.YELLOW,
                )
            )
    else:
        typer.echo(
            f"  {ok}  Unaccounted — every changed byte is explained by the recipe."
        )

    typer.echo("")
    if result["clean"]:
        typer.echo(
            typer.style(
                "  ✅ The three artifacts are consistent.", fg=typer.colors.GREEN, bold=True
            )
        )
    else:
        typer.echo(
            typer.style(
                "  ⚠  Inconsistencies found — review the failed verdicts above.",
                fg=typer.colors.YELLOW,
                bold=True,
            )
        )
    typer.echo("")
