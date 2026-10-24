"""
openremap families

List all supported ECU families, or show expanded detail for one family.

Examples:
    openremap families
    openremap families --family EDC16
    openremap families --family ME7
"""

from __future__ import annotations

import typer

from openremap.core.manufacturers.families import FAMILIES as _FAMILIES
from openremap.core.manufacturers.families import find_family as _lookup
from openremap.core.manufacturers.families import normalize as _normalize

# ---------------------------------------------------------------------------
# Embedded family data
# ---------------------------------------------------------------------------
# Each entry is a dict with:
#   name        — canonical display name (also used for --family matching)
#   aliases     — alternative names accepted by --family
#   era         — production era
#   size        — typical file size(s)
#   summary     — one-line description shown in the table
#   vehicles    — representative vehicle / engine list
#   sub_families — list of known sub-variant identifiers
#   identifier  — key fingerprint used by the extractor
#   sw_format   — software version format / prefix
#   hw_in_bin   — whether HW number is embedded in the binary
#   notes       — any extra detail worth calling out
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

_WIDTH = 73
_COL_FAMILY = 22
_COL_ERA = 12
_COL_SIZE = 16


def _sep(dim: bool = True) -> None:
    line = "  " + "─" * _WIDTH
    typer.echo(typer.style(line, dim=dim) if dim else line)


def _blank() -> None:
    typer.echo("")


def _header(text: str) -> None:
    typer.echo(typer.style(f"  {text}", bold=True))


def _row(family: str, era: str, size: str, summary: str) -> None:
    fam_col = typer.style(f"{family:<{_COL_FAMILY}}", fg=typer.colors.CYAN)
    era_col = f"{era:<{_COL_ERA}}"
    size_col = f"{size:<{_COL_SIZE}}"
    typer.echo(f"  {fam_col}  {era_col}  {size_col}  {summary}")


def _kv(key: str, value: str, key_width: int = 14) -> None:
    k = typer.style(f"  {key:<{key_width}}", dim=True)
    typer.echo(f"{k}  {value}")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _print_table() -> None:
    """Print all families as a compact aligned table."""
    _blank()
    _header("Supported ECU Families")
    _blank()
    _sep()

    # Column headers
    fam_hdr = typer.style(f"  {'FAMILY':<{_COL_FAMILY}}", bold=True)
    era_hdr = typer.style(f"{'ERA':<{_COL_ERA}}", bold=True)
    siz_hdr = typer.style(f"{'SIZE':<{_COL_SIZE}}", bold=True)
    dsc_hdr = typer.style("NOTES", bold=True)
    typer.echo(f"{fam_hdr}  {era_hdr}  {siz_hdr}  {dsc_hdr}")
    _sep()

    for fam in _FAMILIES:
        _row(fam["name"], fam["era"], fam["size"], fam["summary"])

    _sep()
    _blank()
    typer.echo(
        "  "
        + typer.style(
            "openremap families --family <NAME>", fg=typer.colors.GREEN, bold=True
        )
        + typer.style("   show full detail for one family", dim=True)
    )
    typer.echo(
        "  "
        + typer.style(
            "openremap identify <FILE>          ", fg=typer.colors.GREEN, bold=True
        )
        + typer.style("   identify an ECU binary", dim=True)
    )
    _blank()


def _print_detail(fam: dict) -> None:
    """Print full detail for a single family."""
    _blank()
    typer.echo(typer.style(f"  {fam['name']}", fg=typer.colors.CYAN, bold=True))
    _sep()
    _blank()

    _kv("Era", fam["era"])
    _kv("File size", fam["size"])
    _blank()

    _kv("Sub-families", fam["sub_families"][0])
    for sf in fam["sub_families"][1:]:
        typer.echo(f"  {'':14}  {sf}")
    _blank()

    _kv("Identifier", fam["identifier"])
    _blank()

    _kv("SW format", fam["sw_format"])
    _kv("HW in binary", fam["hw_in_bin"])
    _blank()

    _kv("Vehicles", fam["vehicles"][0])
    for v in fam["vehicles"][1:]:
        typer.echo(f"  {'':14}  {v}")
    _blank()

    if fam.get("notes"):
        _kv("Notes", "")
        # Wrap notes at ~58 chars for readability
        words = fam["notes"].split()
        line: list[str] = []
        line_len = 0
        for word in words:
            if line_len + len(word) + 1 > 56 and line:
                prefix = "  " + " " * 16
                typer.echo(f"{prefix}{' '.join(line)}")
                line = [word]
                line_len = len(word)
            else:
                line.append(word)
                line_len += len(word) + 1
        if line:
            typer.echo(f"  {'':16}{' '.join(line)}")
        _blank()

    _sep()
    _blank()
    typer.echo(
        "  "
        + typer.style(
            "openremap families             ", fg=typer.colors.GREEN, bold=True
        )
        + typer.style("show all families", dim=True)
    )
    typer.echo(
        "  "
        + typer.style(
            "openremap identify <FILE>      ", fg=typer.colors.GREEN, bold=True
        )
        + typer.style("identify an ECU binary", dim=True)
    )
    _blank()


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def families(
    family: str | None = typer.Option(
        None,
        "--family",
        "-f",
        help=(
            "Show expanded detail for a specific ECU family. "
            "Accepts the family name or any known alias "
            "(e.g. EDC16, ME7, M3x, mp3.2, edc3, lh-jetronic)."
        ),
        metavar="NAME",
    ),
) -> None:
    """
    List all supported ECU families.

    Without --family, prints a compact table of every supported ECU family
    with era, typical file size, and a one-line description.

    With --family <NAME>, prints full detail for that family — sub-variants,
    fingerprint method, SW format, HW availability, representative vehicles,
    and any relevant notes.

    \b
    Examples:
        openremap families
        openremap families --family EDC16
        openremap families --family ME7
        openremap families -f mp3.2
    """
    if family is None:
        _print_table()
        return

    entry = _lookup(family)
    if entry is None:
        typer.echo(
            typer.style(
                f"\n  Error: unknown family '{family}'.\n",
                fg=typer.colors.RED,
                bold=True,
            ),
            err=True,
        )
        # Fuzzy "did you mean" — rapidfuzz over normalized names/aliases.
        from rapidfuzz import process

        choices = [
            (choice, entry)
            for entry in _FAMILIES
            for choice in [entry["name"], *entry["aliases"]]
        ]
        needles = [choice for choice, _entry in choices]
        matches = process.extract(_normalize(family), needles, limit=8, score_cutoff=50)
        seen: list[str] = []
        suggestions: list[str] = []
        for _choice, score, idx in matches:
            fam_name = choices[idx][1]["name"]
            if fam_name in seen:
                continue
            seen.append(fam_name)
            suggestions.append(f"{fam_name} — {score:.0f}%")
            if len(seen) >= 5:
                break
        if suggestions:
            typer.echo(
                "  "
                + typer.style("Closest families: ", fg=typer.colors.YELLOW, bold=True)
                + ", ".join(suggestions),
                err=True,
            )
        typer.echo(
            "  Run "
            + typer.style("openremap families", fg=typer.colors.GREEN, bold=True)
            + " to see all supported families.\n",
            err=True,
        )
        raise typer.Exit(code=1)

    _print_detail(entry)
