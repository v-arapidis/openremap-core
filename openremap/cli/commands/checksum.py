"""
openremap checksum <FILE>

Detect which checksum schemes a binary satisfies, and whether known
schemes are OK or STALE.

Examples:
    openremap checksum ecu.bin
    openremap checksum ecu.bin --json
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from openremap.api.commands.checksum import checksum_binary
from openremap.cli.io import load_binary_file


def checksum_cmd(
    file: Path = typer.Argument(
        ...,
        help="ECU binary to check (.bin/.ori/.hex).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a table.",
    ),
) -> None:
    """
    Detect checksum schemes in an ECU binary.

    Tests a closed config space — 11 algorithm families (byte/word sums,
    XORs, CRC-8/16/32) × init values × final XOR × regions (whole file,
    16/32/64 KB pages) × store locations (file/page ends) × direct /
    two's-complement forms — at native speed.

    A whole-file match is weak evidence (one store location); a per-page
    scheme matching >= 90% of non-erased pages is a strong signal.
    Detection only — no correction.  Most freely-downloaded dumps carry
    stale or stripped checksums and yield no matches (see
    ISSUE-3).
    """
    data, _fmt = load_binary_file(file, "Binary")
    r = checksum_binary(data, file.name)

    if as_json:
        r["file"] = str(file)
        typer.echo(json.dumps(r, indent=2, ensure_ascii=False, sort_keys=True))
        return

    me7 = r["me7_main"]
    ms43 = r["ms43"]
    denso = r["denso"]
    ironfelix = r["ironfelix"]
    rolling = r["me7_rolling"]
    multirange = r["me7_multirange"]
    matches = r["schemes"]

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Checksum Detection", bold=True))
    known = (
        (1 if me7 is not None else 0)
        + len(ironfelix)
        + (1 if ms43 is not None else 0)
    )
    typer.echo(
        typer.style(
            f"  {file.name}  •  {r['file_size']:,} bytes  •  "
            f"{known} known family scheme(s)  •  "
            f"{len(matches)} generic match(es)",
            dim=True,
        ),
    )
    typer.echo("")

    if me7 is not None:
        colour = typer.colors.GREEN if me7["status"] == "ok" else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Bosch ME7 main checksum: {me7['status'].upper()}  "
                f"(stored {me7['stored']}, expected {me7['expected']})",
                fg=colour, bold=True,
            )
        )
        mp = r["me7_multipoint"]
        typer.echo(
            typer.style(
                f"  Bosch ME7 multipoint: {mp['valid']} block(s) verify, "
                f"{mp['bootrom_unverifiable']} bootrom descriptor(s) not verifiable from a "
                f"flash-only dump",
                fg=typer.colors.GREEN,
            )
        )
        if rolling is not None:
            ok = sum(1 for e in rolling if e["status"] == "ok")
            colour = (
                typer.colors.GREEN if ok == len(rolling) else typer.colors.RED
            )
            typer.echo(
                typer.style(
                    f"  Bosch ME7 rolling: {ok}/{len(rolling)} slot(s) verify",
                    fg=colour, bold=True,
                )
            )
        if multirange is not None:
            colour = (
                typer.colors.GREEN
                if multirange["status"] == "ok"
                else typer.colors.RED
            )
            typer.echo(
                typer.style(
                    f"  Bosch ME7 multirange (byte sum): {multirange['status']}",
                    fg=colour, bold=True,
                )
            )
        typer.echo("")

    if ms43 is not None:
        allok = ms43["ok"] == ms43["total"]
        colour = typer.colors.GREEN if allok else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Siemens MS43 CRC16: {ms43['ok']}/{ms43['total']} sections ok",
                fg=colour, bold=True,
            )
        )
        for c in ms43["crcs"]:
            if c["status"] == "ok":
                continue
            typer.echo(
                typer.style(
                    f"      {c['name']}: {c['status']}"
                    f" (stored {c['stored']}, expected {c['expected']})",
                    fg=typer.colors.RED,
                )
            )
        for m in ms43["mons"]:
            typer.echo(
                typer.style(
                    f"      {m['name']} monitor sum @0x{m['slot']:X}: runtime check "
                    f"(not verifiable from a static dump)",
                    fg=typer.colors.CYAN,
                )
            )
        typer.echo("")

    if denso is not None:
        allok = denso["ok"] == denso["total"]
        colour = typer.colors.GREEN if allok else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Denso Subaru descriptor table @0x{denso['table']:X}: "
                f"{denso['ok']}/{denso['total']} entries ok",
                fg=colour, bold=True,
            )
        )
        for e in denso["entries"]:
            if e["status"] == "ok" or e["status"] == "disabled":
                continue
            typer.echo(
                typer.style(
                    f"      entry {e['index']} [{e['start']:X}, {e['end']:X}]: {e['status']}"
                    f" (stored {e['stored']}, expected {e['expected']})",
                    fg=typer.colors.RED,
                )
            )
        typer.echo("")

    if ironfelix:
        typer.echo(typer.style("  IronFelix family profiles", bold=True))
        typer.echo(typer.style("  " + "─" * 74, dim=True))
        for p in ironfelix:
            label = p["description"] + (f" (subtype {p['subtype']})" if p["subtype"] else "")
            allok = p["checks_ok"] == p["checks_total"]
            colour = typer.colors.GREEN if allok else typer.colors.YELLOW
            mp = ""
            if p["multipoint_valid"] or p["multipoint_unverified"]:
                mp = (
                    f"  multipoint {p['multipoint_valid']} ok / "
                    f"{p['multipoint_unverified']} unverifiable"
                )
            typer.echo(
                f"  {label:<34} "
                + typer.style(
                    f"{p['checks_ok']}/{p['checks_total']} checks ok", fg=colour, bold=allok,
                )
                + mp
            )
            for c in p["checks"]:
                if c["status"] == "ok":
                    continue
                col = typer.colors.RED if c["status"] == "stale" else typer.colors.CYAN
                typer.echo(
                    typer.style(
                        f"      {c['name']}: {c['status']}"
                        + (
                            f" (stored {c['stored']}, expected {c['expected']})"
                            if c["status"] == "stale"
                            else ""
                        ),
                        fg=col,
                    )
                )
        typer.echo("")

    if not matches:
        typer.echo("")
        return

    typer.echo(
        typer.style(
            f"  {'Algo':<11} {'Init':>8} {'Xor':>8} {'Region':>8} "
            f"{'Store':>10} {'Form':>11} {'Pages':>9}",
            bold=True,
        )
    )
    typer.echo(typer.style("  " + "─" * 74, dim=True))
    for m in matches:
        pages = (
            f"{m['pages_matched']}/{m['pages_total']}"
            if m["region"].startswith("page")
            else "—"
        )
        form = "complement" if m["complement"] else "direct"
        strong = m["rate"] >= _RATE and m["region"].startswith("page")
        typer.echo(
            f"  {m['algo']:<11} "
            f"0x{m['init']:06X}  0x{m['final_xor']:06X}  "
            f"{m['region']:<8} {m['store']:<10} "
            + typer.style(f"{form:<11}", fg=typer.colors.GREEN if strong else typer.colors.YELLOW)
            + f" {pages:>9}"
        )
    typer.echo("")


_RATE = 0.9
