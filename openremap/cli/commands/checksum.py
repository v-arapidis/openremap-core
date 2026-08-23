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

from openremap.core.services.checksums.checksum import (
    detect_me7_multipoint,
    detect_me7_multipoint_unverified,
    sweep,
    verify_me7,
)
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import detect_all as detect_ironfelix
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.checksums.nefmoto import (
    detect_me7_multirange,
    detect_me7_rolling,
)


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
    try:
        data = file.read_bytes()
    except OSError as exc:
        typer.echo(
            typer.style(
                f"Error: cannot read '{file.name}': {exc}",
                fg=typer.colors.RED, bold=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1)

    matches = sweep(data)
    me7 = verify_me7(data)
    mp_valid = detect_me7_multipoint(data) if me7 is not None else []
    mp_bootrom = sum(
        1
        for b in detect_me7_multipoint_unverified(data)
        if b.start < 0x20000
    ) if me7 is not None else 0
    ironfelix = detect_ironfelix(data)
    rolling = detect_me7_rolling(data)
    multirange = detect_me7_multirange(data)
    ms43 = detect_ms43(data)
    denso = detect_denso(data)

    if as_json:
        out = {
            "file": str(file),
            "file_size": len(data),
            "me7_main": (
                {
                    "status": me7.status,
                    "stored": me7.stored_hex,
                    "expected": me7.expected_hex,
                }
                if me7 is not None
                else None
            ),
            "me7_multipoint": (
                {
                    "valid": len(mp_valid),
                    "bootrom_unverifiable": mp_bootrom,
                }
                if me7 is not None
                else None
            ),
            "me7_rolling": (
                [
                    {
                        "store": e.store_offset,
                        "status": e.status,
                        "stored": f"{e.stored:08X}",
                        "expected": f"{e.expected:08X}",
                        "ranges": [
                            {"start": r.start, "end": r.end} for r in e.ranges
                        ],
                        "init_range": (
                            {"start": e.init_range.start, "end": e.init_range.end}
                            if e.init_range is not None
                            else None
                        ),
                    }
                    for e in rolling
                ]
                if rolling is not None
                else None
            ),
            "me7_multirange": (
                {
                    "store": multirange.store_offset,
                    "status": multirange.status,
                    "stored": f"{multirange.stored:08X}",
                    "expected": f"{multirange.expected:08X}",
                    "ranges": [
                        {"start": r.start, "end": r.end} for r in multirange.ranges
                    ],
                }
                if multirange is not None
                else None
            ),
            "ms43": (
                {
                    "crcs": [
                        {
                            "name": c.name,
                            "slot": c.slot,
                            "status": c.status,
                            "stored": f"{c.stored:04X}" if c.stored is not None else None,
                            "expected": f"{c.expected:04X}" if c.expected is not None else None,
                            "blocks": [{"start": s, "end": e} for s, e in c.blocks],
                        }
                        for c in ms43.crcs
                    ],
                    "mons": [
                        {"name": m.name, "slot": m.slot, "status": m.status,
                         "stored": f"{m.stored:08X}" if m.stored is not None else None}
                        for m in ms43.mons
                    ],
                    "ok": ms43.ok,
                    "total": ms43.total,
                }
                if ms43 is not None
                else None
            ),
            "denso": (
                {
                    "table": denso.table_offset,
                    "status": denso.status,
                    "ok": denso.ok,
                    "total": denso.total,
                    "entries": [
                        {
                            "index": e.index,
                            "start": e.start,
                            "end": e.end,
                            "status": e.status,
                            "stored": f"{e.stored:08X}" if e.stored is not None else None,
                            "expected": f"{e.expected:08X}" if e.expected is not None else None,
                        }
                        for e in denso.entries
                    ],
                }
                if denso is not None
                else None
            ),
            "ironfelix": [                {
                    "family": p.family,
                    "description": p.description,
                    "subtype": p.subtype,
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "stored": c.stored_hex,
                            "expected": c.expected_hex,
                        }
                        for c in p.checks
                    ],
                    "checks_ok": p.ok,
                    "checks_total": p.total,
                    "multipoint_valid": p.multipoint_valid,
                    "multipoint_unverified": p.multipoint_unverified,
                }
                for p in ironfelix
            ],
            "schemes": [
                {
                    "algo": m.scheme.algo,
                    "init": m.scheme.init,
                    "final_xor": m.scheme.final_xor,
                    "region": m.scheme.region,
                    "exclude_tail": m.scheme.exclude_tail,
                    "store": m.scheme.store,
                    "store_le": m.scheme.store_le,
                    "complement": m.scheme.complement,
                    "pages_matched": m.pages_matched,
                    "pages_total": m.pages_total,
                    "rate": round(m.rate, 3),
                }
                for m in matches
            ],
        }
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
        return

    typer.echo("")
    typer.echo(typer.style("  OpenRemap — Checksum Detection", bold=True))
    known = (
        (1 if me7 is not None else 0)
        + len(ironfelix)
        + (1 if ms43 is not None else 0)
    )
    typer.echo(
        typer.style(
            f"  {file.name}  •  {len(data):,} bytes  •  "
            f"{known} known family scheme(s)  •  "
            f"{len(matches)} generic match(es)",
            dim=True,
        ),
    )
    typer.echo("")

    if me7 is not None:
        colour = typer.colors.GREEN if me7.status == "ok" else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Bosch ME7 main checksum: {me7.status.upper()}  "
                f"(stored {me7.stored_hex}, expected {me7.expected_hex})",
                fg=colour, bold=True,
            )
        )
        typer.echo(
            typer.style(
                f"  Bosch ME7 multipoint: {len(mp_valid)} block(s) verify, "
                f"{mp_bootrom} bootrom descriptor(s) not verifiable from a "
                f"flash-only dump",
                fg=typer.colors.GREEN,
            )
        )
        if rolling is not None:
            ok = sum(1 for e in rolling if e.status == "ok")
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
                if multirange.status == "ok"
                else typer.colors.RED
            )
            typer.echo(
                typer.style(
                    f"  Bosch ME7 multirange (byte sum): {multirange.status}",
                    fg=colour, bold=True,
                )
            )
        typer.echo("")

    if ms43 is not None:
        allok = ms43.ok == ms43.total
        colour = typer.colors.GREEN if allok else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Siemens MS43 CRC16: {ms43.ok}/{ms43.total} sections ok",
                fg=colour, bold=True,
            )
        )
        for c in ms43.crcs:
            if c.status == "ok":
                continue
            typer.echo(
                typer.style(
                    f"      {c.name}: {c.status}"
                    f" (stored {c.stored:04X}, expected {c.expected:04X})",
                    fg=typer.colors.RED,
                )
            )
        for m in ms43.mons:
            typer.echo(
                typer.style(
                    f"      {m.name} monitor sum @0x{m.slot:X}: runtime check "
                    f"(not verifiable from a static dump)",
                    fg=typer.colors.CYAN,
                )
            )
        typer.echo("")

    if denso is not None:
        allok = denso.ok == denso.total
        colour = typer.colors.GREEN if allok else typer.colors.RED
        typer.echo(
            typer.style(
                f"  Denso Subaru descriptor table @0x{denso.table_offset:X}: "
                f"{denso.ok}/{denso.total} entries ok",
                fg=colour, bold=True,
            )
        )
        for e in denso.entries:
            if e.status == "ok" or e.status == "disabled":
                continue
            typer.echo(
                typer.style(
                    f"      entry {e.index} [{e.start:X}, {e.end:X}]: {e.status}"
                    f" (stored {e.stored:08X}, expected {e.expected:08X})",
                    fg=typer.colors.RED,
                )
            )
        typer.echo("")

    if ironfelix:
        typer.echo(typer.style("  IronFelix family profiles", bold=True))
        typer.echo(typer.style("  " + "─" * 74, dim=True))
        for p in ironfelix:
            label = p.description + (f" (subtype {p.subtype})" if p.subtype else "")
            allok = p.ok == p.total
            colour = typer.colors.GREEN if allok else typer.colors.YELLOW
            mp = ""
            if p.multipoint_valid or p.multipoint_unverified:
                mp = (
                    f"  multipoint {p.multipoint_valid} ok / "
                    f"{p.multipoint_unverified} unverifiable"
                )
            typer.echo(
                f"  {label:<34} "
                + typer.style(
                    f"{p.ok}/{p.total} checks ok", fg=colour, bold=allok,
                )
                + mp
            )
            for c in p.checks:
                if c.status == "ok":
                    continue
                col = typer.colors.RED if c.status == "stale" else typer.colors.CYAN
                typer.echo(
                    typer.style(
                        f"      {c.name}: {c.status}"
                        + (
                            f" (stored {c.stored_hex}, expected {c.expected_hex})"
                            if c.status == "stale"
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
            f"{m.pages_matched}/{m.pages_total}"
            if m.scheme.region.startswith("page")
            else "—"
        )
        form = "complement" if m.scheme.complement else "direct"
        strong = m.rate >= _RATE and m.scheme.region.startswith("page")
        typer.echo(
            f"  {m.scheme.algo:<11} "
            f"0x{m.scheme.init:06X}  0x{m.scheme.final_xor:06X}  "
            f"{m.scheme.region:<8} {m.scheme.store:<10} "
            + typer.style(f"{form:<11}", fg=typer.colors.GREEN if strong else typer.colors.YELLOW)
            + f" {pages:>9}"
        )
    typer.echo("")


_RATE = 0.9
