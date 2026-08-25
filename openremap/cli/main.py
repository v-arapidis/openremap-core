"""
OpenRemap CLI — root entry point.

Dispatching rules:
    openremap                          → launches the TUI
    openremap --help                   → CLI help
    openremap --version                → version string
    openremap <command> [args...]      → CLI command

Usage:
    openremap
    openremap --help
    openremap --version
    openremap commands
    openremap workflow
    openremap families
    openremap families --family EDC16
    openremap identify ecu.bin
    openremap cook stock.bin stage1.bin --output recipe.remap
    openremap tune target.bin recipe.remap
    openremap tune target.bin recipe.remap --output target_tuned.bin
    openremap validate before target.bin recipe.remap
    openremap validate check  target.bin recipe.remap
    openremap validate after  target_tuned.bin recipe.remap
    openremap scan ./my_bins/
    openremap scan ./my_bins/ --move --organize
"""

import sys
from typing import Optional

import typer

from openremap.cli.commands import validate
from openremap.cli.commands.analyze import analyze
from openremap.cli.commands.cmds import commands
from openremap.cli.commands.convert import convert
from openremap.cli.commands.cook import cook
from openremap.cli.commands.cook_volatile import cook_volatile
from openremap.cli.commands.families import families
from openremap.cli.commands.identify import identify
from openremap.cli.commands.scan import scan
from openremap.cli.commands.diff_maps import diff_maps
from openremap.cli.commands.audit import audit_cmd
from openremap.cli.commands.checksum import checksum_cmd
from openremap.cli.commands.health import health_cmd
from openremap.cli.commands.merge import merge
from openremap.cli.commands.layout import layout
from openremap.cli.commands.scan_maps import scan_maps
from openremap.cli.commands.scan_vins import scan_vins_cmd
from openremap.cli.commands.tune import tune
from openremap.cli.commands.workflow import workflow

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="openremap",
    help=(
        "OpenRemap — ECU binary analysis and patching toolkit.\n\n"
        "Diff, validate, and apply tuning recipes to automotive ECU binaries "
        "without a running API server.\n\n"
        "Run with no arguments to launch the full terminal UI.\n"
        "New here?  Run  openremap workflow  for a plain-English step-by-step guide.\n"
        "Quick reminder?  Run  openremap commands  for a one-line-per-command cheat-sheet."
    ),
    no_args_is_help=True,
    pretty_exceptions_enable=True,
    pretty_exceptions_show_locals=False,
)

# ---------------------------------------------------------------------------
# Version flag
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        from openremap import __version__, _active_backend
        backend = _active_backend()
        suffix = f" ({backend})" if backend != "unknown" else ""
        typer.echo(f"openremap {__version__}{suffix}")
        raise typer.Exit()


@app.callback()
def _callback(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

# workflow, identify, cook, tune, scan, families, and commands are single-action
# commands — registered directly to avoid the Typer 0.12+ regression where
# @app.callback(invoke_without_command=True) with typer.Argument parameters
# fails when added via add_typer().

app.command(
    name="commands",
    help="Print a one-line-per-command cheat-sheet of every available command.",
)(commands)

app.command(
    name="workflow",
    help=(
        "Print a complete step-by-step workflow guide — plain English, "
        "with commands, expected output, and what to do when something goes wrong. "
        "Start here if you are new to OpenRemap or the terminal."
    ),
)(workflow)

app.command(
    name="families",
    help=(
        "List all supported ECU families with era, file size, and notes. "
        "Use --family <NAME> for full detail on a specific family."
    ),
)(families)

app.command(
    name="identify",
    help="Identify an ECU binary — manufacturer, family, software version, and more.",
    no_args_is_help=True,
)(identify)

app.command(
    name="analyze",
    help=(
        "Describe a whole ECU binary in one pass: container, identity + "
        "confidence, VIN, flash layout, maps, checksums, health verdict."
    ),
    no_args_is_help=True,
)(analyze)

app.command(
    name="convert",
    help=(
        "Normalise an ECU binary image to flat bytes: real Intel HEX and "
        "S-Record files are parsed (addresses + checksums validated), raw "
        "dumps pass through unchanged."
    ),
    no_args_is_help=True,
)(convert)

app.command(
    name="audit",
    help="The receipt check: verify stock, tuned, and recipe belong together.",
    no_args_is_help=True,
)(audit_cmd)

app.command(
    name="layout",
    help="Print the flash-layout block map of an ECU binary (erased/code/calibration/ident).",
    no_args_is_help=True,
)(layout)

app.command(
    name="merge",
    help="Merge two recipes into one, validated against a common stock binary.",
    no_args_is_help=True,
)(merge)

app.command(
    name="cook",
    help="Cook a recipe by diffing an original and a modified ECU binary.",
    no_args_is_help=True,
)(cook)

app.command(
    name="cook-volatile",
    help=(
        "Cook a car-portable recipe: diff two binaries, detect volatile "
        "instructions (VIN, checksum stores), exclude the near-certain "
        "ones, and record evidence in a 'volatile' recipe section."
    ),
    no_args_is_help=True,
)(cook_volatile)

# validate has real sub-commands (before / check / after) and uses
# @app.command() internally — add_typer works correctly for it.
app.add_typer(validate.app, name="validate")

app.command(
    name="tune",
    help=(
        "One-shot: validate before → apply recipe → validate after. "
        "The original file is never modified — the tuned result is written separately. "
        "Run  openremap validate check  if Phase 1 fails to diagnose why."
    ),
    no_args_is_help=True,
)(tune)

app.command(
    name="scan",
    help=(
        "Batch-scan a directory of ECU binaries through all registered extractors.\n\n"
        "Each file is classified and optionally moved into one of five sub-folders: "
        "scanned, sw_missing, contested, unknown, or trash."
    ),
)(scan)

app.command(
    name="checksum",
    help="Detect which checksum schemes a binary satisfies (OK/STALE detection, no correction).",
    no_args_is_help=True,
)(checksum_cmd)

app.command(
    name="health",
    help="One-shot calibration health check: identity, checksums, axis sanity, map-count envelope, erased blocks, VINs. CI-gateable (--json).",
    no_args_is_help=True,
)(health_cmd)

app.command(
    name="scan-vins",
    help="Locate VIN candidates in an ECU binary and score them.",
    no_args_is_help=True,
)(scan_vins_cmd)

app.command(
    name="scan-maps",
    help=(
        "Scan an ECU binary for plausible calibration map axes and 2D tables.\n\n"
        "Structural scan — no manufacturer identification needed. "
        "Finds monotonically-increasing 16-bit sequences (RPM/load breakpoints) "
        "and the rectangular data blocks that follow them. "
        "Useful for health-checking binaries and discovering maps in unsupported ECUs."
    ),
    no_args_is_help=True,
)(scan_maps)

app.command(
    name="diff-maps",
    help=(
        "Diff calibration maps between a stock and tuned ECU binary.\n\n"
        "Scans both files, matches maps by axis fingerprint, and reports "
        "cell-by-cell changes for each matched pair. "
        "Useful for auditing a tune: which maps were modified, how aggressive "
        "each change is, and whether anything looks suspicious."
    ),
    no_args_is_help=True,
)(diff_maps)


# ---------------------------------------------------------------------------
# Smart dispatcher — bare `openremap` → TUI, anything else → CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``openremap`` console script.

    When invoked with no arguments the TUI is launched.  Any arguments or
    flags (``--help``, ``--version``, subcommands, etc.) are forwarded to
    the Typer CLI app as before.
    """
    if len(sys.argv) == 1:
        # No arguments at all → launch the TUI
        from openremap.tui.main import run as _run_tui

        _run_tui()
    else:
        app()


if __name__ == "__main__":
    main()
