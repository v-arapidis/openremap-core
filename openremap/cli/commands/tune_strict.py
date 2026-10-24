"""
openremap tune-strict <target> <recipe> [--output <out>]

Apply a recipe **only when every check passes**. Unlike ``tune`` there is no
``--force`` and no ``--skip-validation``: the command *is* the policy.

The checks, in order:

  recipe     valid recipe, known schema, no unknown fields, contract agrees
  floor      the recipe's identity floor is respected (stricter is always fine)
  size       target size equals the declared source size
  identity   target and recipe identify as the SAME firmware (match_key)
  bytes      every original byte is at its recorded offset ("already patched"
             gets its own message)
  anchors    ctx+ob+context_after found within +/-2048 bytes, all-or-nothing
  shift      every anchor found exactly at the expected offset
  verify     the patched result re-checks, and every changed byte is inside a
             declared range

Reported but never decisive: the same-file-only stamp, recipe statistics,
flash-region split, and the number of anchor look-alikes in the search window.

The original file is never modified. Exit code 0 = applied, 1 = refused,
2 = bad input.

Examples:
    openremap tune-strict target.bin stage1.remap
    openremap tune-strict target.bin stage1.remap --output car2_tuned.bin
    openremap tune-strict target.bin stage1.remap --json
"""

from __future__ import annotations

import base64
import json
import re
import textwrap
from pathlib import Path
from typing import Annotated

import typer

from openremap.cli.io import load_binary_file
from openremap.core.services.recipes.apply.engine import apply_strict
from openremap.core.services.recipes.apply.report import PASS, REFUSE

_ALLOWED_BIN = (".bin", ".ori", ".hex", ".s19", ".srec", ".mot")
_ALLOWED_RECIPE = (".remap", ".openremap", ".json")
_W = 62


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def _read_bin(path: Path, label: str) -> bytes:
    if path.suffix.lower() not in _ALLOWED_BIN:
        typer.echo(
            typer.style(
                f"Error: {label} file '{path.name}' must be a .bin, .ori, .hex, "
                ".s19, .srec, or .mot file.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2)
    data, _fmt = load_binary_file(path, label)
    return data


def _read_recipe(path: Path) -> dict:
    if path.suffix.lower() not in _ALLOWED_RECIPE:
        typer.echo(
            typer.style(
                f"Error: recipe file '{path.name}' must be .remap, .openremap or .json.",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        typer.echo(
            typer.style(f"Error reading recipe: {exc}", fg=typer.colors.RED, bold=True),
            err=True,
        )
        raise typer.Exit(code=2)
    except json.JSONDecodeError as exc:
        typer.echo(
            typer.style(
                f"Error: recipe '{path.name}' is not valid JSON: {exc}",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=2)


def _default_output(target: Path) -> Path:
    return target.parent / f"{target.stem}_tuned{target.suffix}"


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

_VERDICT_COLOUR = {
    PASS: typer.colors.GREEN,
    REFUSE: typer.colors.RED,
}


def _print_report(result: dict, target: Path, output: Path | None) -> None:
    report = result["report"]
    typer.echo("")
    command = report.get("command", "tune-strict")
    typer.echo(typer.style(f"  {command}  ·  {target.name}", bold=True))
    typer.echo(typer.style("  " + "─" * _W, fg=typer.colors.BRIGHT_BLACK))

    policy = report.get("policy", {})
    typer.echo(
        f"  POLICY        identity={policy.get('identity')} · "
        f"positions={policy.get('positions')} · "
        f"shift={policy.get('shift')} · ambiguity={policy.get('ambiguity')}"
    )
    if report.get("schema"):
        typer.echo(f"  SCHEMA        {report['schema']}")

    identity = report.get("identity") or {}
    if identity:
        typer.echo(f"  RECIPE KEY    {identity.get('recipe_match_key')}")
        typer.echo(f"  TARGET KEY    {identity.get('target_match_key')}")

    typer.echo("")
    for check in report.get("checks", []):
        colour = _VERDICT_COLOUR.get(check["verdict"])
        mark = check["verdict"].upper()
        if colour:
            mark = typer.style(mark, fg=colour, bold=True)
        typer.echo(f"  {check['id']:<10} {mark:<8} {check['detail']}")

    typer.echo("")
    refusal = report.get("refusal")
    if refusal:
        typer.echo(
            typer.style(
                f"  ✗ REFUSED at {refusal['check']}", fg=typer.colors.RED, bold=True
            )
        )
        for line in str(refusal["message"]).splitlines():
            typer.echo(f"    {line}")
        if report.get("next_step"):
            typer.echo(typer.style(f"  → {report['next_step']}", fg=typer.colors.CYAN))
        typer.echo("")
        return

    res = report.get("result", {})
    typer.echo(typer.style("  ✅ APPLIED", fg=typer.colors.GREEN, bold=True))
    typer.echo(f"  sha256 in     {res.get('input_sha256')}")
    typer.echo(f"  sha256 out    {res.get('output_sha256')}")
    typer.echo(f"  bytes written {res.get('bytes_written'):,}")
    if output:
        typer.echo(typer.style(f"  saved to      {output}", fg=typer.colors.CYAN))
    _print_checksums(res.get("checksums") or {}, output)
    _print_disclaimer(res.get("disclaimer"))
    typer.echo("")


#: How a checksum verdict reads to an operator. 'APPLIED' is a statement about the
#: recipe; this is the statement about the *file*, and they are not the same thing
#: — nothing in this engine rewrites a stored checksum.
_CHECKSUM_STATUS = {
    "ok": ("every scheme we know still verifies", typer.colors.GREEN),
    "fail": ("STALE — the patch broke a checksum we can verify", typer.colors.RED),
    "skip": (
        "no known scheme for this family — nothing was checked",
        typer.colors.YELLOW,
    ),
}


def _print_checksums(checks: dict, output: Path | None) -> None:
    """The disclaimer, measured rather than boilerplate.

    A patched file that verifies byte-for-byte against the recipe can still be
    unflashable, because a checksum stored over the changed region is now stale.
    Saying so here is the only place the operator can learn it before the ECU
    tells them.

    Three short lines rather than one long one: at 80 columns the verdict and the
    verdict's reason together ran off the edge, and this is the last thing printed.
    """
    if not checks:
        return
    phrase, colour = _CHECKSUM_STATUS.get(
        checks.get("status"), (checks.get("message") or "unknown", typer.colors.YELLOW)
    )
    typer.echo(typer.style("  checksums     NOT CORRECTED", fg=colour, bold=True))
    typer.echo(typer.style(f"                {phrase}", fg=colour))
    if checks.get("status") == "fail" and checks.get("message"):
        typer.echo(typer.style(f"                {checks['message']}", fg=colour))
    name = output.name if output else "<written file>"
    typer.echo(
        typer.style(
            f"                verify with  openremap checksum {name}",
            fg=typer.colors.BRIGHT_BLACK,
        )
    )


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def emit_result(
    result: dict,
    target: Path,
    output: Path | None,
    json_output: bool,
    report_output: Path | None,
) -> None:
    """Write the report/output, print, and exit non-zero when refused.

    Shared by every apply command (``tune-strict`` / ``-shift`` / ``-unique`` /
    ``-any``) so the four stay identical in presentation and exit codes.
    """
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


def tune_strict(
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
    """Apply a recipe only if every check passes. No flags loosen it."""
    target_data = _read_bin(target, "target")
    recipe_dict = _read_recipe(recipe)
    result = apply_strict(target_data, recipe_dict, target.name, recipe.name)
    emit_result(result, target, output, json_output, report_output)


#: The label column every row of the result block shares.
_ROW = "  " + " " * 14


def _sentence_lines(text: str, width: int) -> list[str]:
    """Wrap *text* to *width*, one sentence per group.

    Sentence-wise rather than document-wise: a disclaimer that breaks in the
    middle of an unrelated clause is the kind of text people skip.
    """
    lines: list[str] = []
    for sentence in re.split(r"(?<=\.)\s+", text):
        lines.extend(textwrap.wrap(sentence, width) or [sentence])
    return lines


def _print_disclaimer(text: str | None) -> None:
    """Cyan, and set apart: this is not another check result.

    ``APPLIED`` and the checksum verdict are statements about a file; this is the
    boundary of what the file may be used for, and the one line a reader must not
    skim past.
    """
    if not text:
        return
    colour = typer.colors.CYAN
    lines = _sentence_lines(text, width=80 - len(_ROW))
    typer.echo(
        typer.style(f"{_ROW[:2]}disclaimer    ", fg=colour, bold=True)
        + typer.style(lines[0], fg=colour)
    )
    for line in lines[1:]:
        typer.echo(typer.style(_ROW + line, fg=colour))
