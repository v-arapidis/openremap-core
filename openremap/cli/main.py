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

import typer

from openremap.cli.commands import contribute, validate
from openremap.cli.commands.analyze import analyze
from openremap.cli.commands.audit import audit_cmd
from openremap.cli.commands.checksum import checksum_cmd
from openremap.cli.commands.cmds import commands
from openremap.cli.commands.convert import convert
from openremap.cli.commands.cook_forced import cook_forced
from openremap.cli.commands.cook_strict import cook_strict
from openremap.cli.commands.diff_maps import diff_maps
from openremap.cli.commands.families import families
from openremap.cli.commands.health import health_cmd
from openremap.cli.commands.identify import identify
from openremap.cli.commands.layout import layout
from openremap.cli.commands.map_image import map_image_cmd
from openremap.cli.commands.merge import merge
from openremap.cli.commands.recipe_info import recipe_info_cmd
from openremap.cli.commands.routine import routine
from openremap.cli.commands.scan import scan
from openremap.cli.commands.scan_maps import scan_maps
from openremap.cli.commands.scan_vins import scan_vins_cmd
from openremap.cli.commands.synth import synth
from openremap.cli.commands.tune_any import tune_any
from openremap.cli.commands.tune_shift import tune_shift
from openremap.cli.commands.tune_strict import tune_strict
from openremap.cli.commands.tune_unique import tune_unique
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
    version: bool | None = typer.Option(
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

# validate has real sub-commands (before / check / after) and uses
# @app.command() internally — add_typer works correctly for it.
app.add_typer(validate.app, name="validate")
app.add_typer(contribute.app, name="contribute")

app.command(
    name="cook-strict",
    help=(
        "Build a schema-5.0 recipe requiring unique anchors. The result declares "
        "identity_floor: any, so every apply command can use it. Nothing is "
        "excluded; suspicious changes become flags."
    ),
    no_args_is_help=True,
)(cook_strict)

app.command(
    name="cook-forced",
    help=(
        "Build a schema-5.0 recipe accepting non-unique anchors (a change inside "
        "blank flash/padding). Declares identity_floor: firmware — only the "
        "match_key-verifying apply commands will accept it."
    ),
    no_args_is_help=True,
)(cook_forced)

app.command(
    name="tune-strict",
    help=(
        "Apply a recipe ONLY when every check passes: valid recipe, same "
        "firmware (match_key), original bytes at their exact offsets, anchor "
        "found without shift, verified result. "
        "There is no --force and no --skip-validation: the command is the policy. "
        "Exit 0 = applied, 1 = refused, 2 = bad input."
    ),
    no_args_is_help=True,
)(tune_strict)

app.command(
    name="tune-shift",
    help=(
        "Apply a recipe when the target is the SAME firmware but the change is "
        "found at a displaced offset (±2048 bytes) instead of the recorded one. "
        "One rung looser than tune-strict: shift is allowed and reported, and in "
        "exchange a non-unique anchor is a refusal. "
        "Exit 0 = applied, 1 = refused, 2 = bad input."
    ),
    no_args_is_help=True,
)(tune_shift)

app.command(
    name="tune-unique",
    help=(
        "Apply a recipe to a target that cannot be identified, as long as the "
        "original bytes are exactly at their recorded offsets AND every anchor is "
        "unique in the search window — two independent proofs of position. "
        "Exit 0 = applied, 1 = refused, 2 = bad input."
    ),
    no_args_is_help=True,
)(tune_unique)

app.command(
    name="tune-any",
    help=(
        "Loosest apply: no identity check, displaced anchors accepted, ambiguous "
        "anchors reported instead of refused. It will write into a different "
        "firmware — read the identity line in the report before flashing. "
        "Exit 0 = applied, 1 = refused, 2 = bad input."
    ),
    no_args_is_help=True,
)(tune_any)

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
    name="synth",
    help=(
        "Generate a synthetic ECU-like binary from scratch — no ECU data needed.\n\n"
        "Deterministic (same parameters + seed = same file), so it can be used "
        "as a test fixture, committed, or sent with a bug report. Generated "
        "files identify as Unknown by design: they carry no manufacturer "
        "fingerprints."
    ),
)(synth)

app.command(
    name="routine",
    help=(
        "Read a code routine as readable pseudo-code (no Ghidra needed).\n\n"
        "Decodes the instruction stream around a file offset and renders each "
        "instruction as one line — a phrasebook decompiler, not full C. "
        "Decoder is picked from the ECU family (Rust C166 or capstone), "
        "override with --arch."
    ),
    no_args_is_help=True,
)(routine)

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
    name="recipe-info",
    help=(
        "Say what a .remap recipe declares — schema, instruction count, identity "
        "floor, and the two files it was cooked from.\n\n"
        "Reads the recipe alone: no target binary needed, nothing is applied."
    ),
    no_args_is_help=True,
)(recipe_info_cmd)

app.command(
    name="map-image",
    help=(
        "Draw one detected calibration table as an SVG picture.\n\n"
        "Every cell's value, coloured, with the breakpoints stored in the binary "
        "and a colour scale. A terminal cannot draw a map honestly, so the "
        "picture is a file you can open, print or send.\n\n"
        "Use --list to see which tables a dump has."
    ),
    no_args_is_help=True,
)(map_image_cmd)

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

    With no arguments, launch the TUI — its HOME screen shows the tasks that
    exist and marks the ones still being built, so it is never a dead end.
    """
    # Before a single character is printed: on Windows a redirected stream would
    # otherwise write cp1252 and die on the first arrow or check mark.
    from openremap.console import force_utf8

    force_utf8()
    if len(sys.argv) == 1:
        from openremap.tui import run as run_tui

        run_tui()
        return
    app()


if __name__ == "__main__":
    main()
