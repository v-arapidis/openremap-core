"""
``openremap contribute`` — the contribution wizard.

Two verbs:

    openremap contribute extractor dump.bin --brand Siemens --family SID206
    openremap contribute extractor a.bin --also b.bin --brand Bosch --family M9.9
    openremap contribute check openremap/core/manufacturers/siemens/sid206/extractor.py

``extractor`` measures your dump(s) and — with ``--write`` — produces the four
artifacts a pull request needs. ``check`` runs the same review bar over an
extractor that already exists, which is what a reviewer runs on a branch.

Nothing here re-implements the engine: the measurement, the rendering and the
verification all live in ``openremap.core.services.wizard``, and this module only
formats and writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer

from openremap.api import call
from openremap.api.errors import ApiError

app = typer.Typer(
    help=(
        "Contribute a new ECU family.\n\n"
        "Measures your own dump(s) for a signature that is stable in your family "
        "and absent from every family the engine already recognises, then writes "
        "the fixture, an extractor skeleton, a test and a PR report. It never "
        "asks for your dump: binaries stay out of the repository by design."
    ),
    no_args_is_help=True,
)


def _fail(message: str) -> None:
    typer.echo(typer.style(f"Error: {message}", fg=typer.colors.RED, bold=True), err=True)
    raise typer.Exit(code=1)


def _rule(title: str) -> None:
    typer.echo("")
    typer.echo(typer.style(title, fg=typer.colors.BRIGHT_WHITE, bold=True))


@app.command(
    name="extractor",
    help="Measure your dump(s) and (with --write) generate the contribution files.",
    no_args_is_help=True,
)
def contribute_extractor(
    dump: Path = typer.Argument(..., help="Your dump (the family you want added)."),
    brand: Optional[str] = typer.Option(
        None,
        "--brand",
        help="Manufacturer name, e.g. Siemens. Required unless --measure-only.",
    ),
    family: Optional[str] = typer.Option(
        None,
        "--family",
        help="Family key, e.g. SID206. Required unless --measure-only — a first "
        "look should not need the answer it is trying to produce.",
    ),
    also: Optional[List[Path]] = typer.Option(
        None,
        "--also",
        help="Another dump of the SAME family. Two or more dumps are how marker "
        "stability is actually verified, so this is worth the extra file.",
    ),
    marker: Optional[str] = typer.Option(
        None, "--marker", help="Use this signature instead of the recommended one."
    ),
    marker_hex: Optional[str] = typer.Option(
        None, "--marker-hex", help="Same, for signatures with non-ASCII bytes."
    ),
    write: bool = typer.Option(
        False, "--write", help="Write the generated files into the repository."
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Directory for the report when --write is used."
    ),
    measure_only: bool = typer.Option(
        False,
        "--measure-only",
        help="Measure without the collision check. Needs no checkout, recommends "
        "no marker, and writes nothing — for a first look at an unsupported dump.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the raw result as JSON."),
) -> None:
    """Measure, render, and (optionally) write the contribution artifacts.

    Without ``--measure-only`` this needs a checkout of openremap-core (the
    collision check needs the committed fixtures in ``_dev/contrib/recipes/``).

    Examples:
        openremap contribute extractor dump.bin --brand Siemens --family SID206
        openremap contribute extractor a.bin --also b.bin --brand Bosch --family M9.9 --write
        openremap contribute extractor dump.bin --brand Siemens --family SID206 --measure-only
    """
    if not measure_only and (not brand or not family):
        # A first look is exactly the case where the user does not know the
        # family name yet, so requiring it there would block the one command
        # they can run without any prior knowledge.
        _fail(
            "--brand and --family are required to generate a contribution: they "
            "name the family the extractor will declare.\n\n"
            "  Do not know them yet? Add --measure-only for a first look that "
            "needs neither."
        )
        return

    paths = [str(dump)] + [str(p) for p in (also or [])]
    params = {"paths": paths}
    if brand:
        params["brand"] = brand
    if family:
        params["family"] = family
    if measure_only:
        params["measure_only"] = True
    if marker:
        params["marker"] = marker
    if marker_hex:
        params["marker_hex"] = marker_hex

    try:
        result = call("contribute_extractor", params)
    except ApiError as exc:
        message = str(exc)
        if "no fixture recipes found" in message:
            # Say what to do about it, and do not point at the command that just
            # failed: the strict mode is the default on purpose.
            _fail(
                message
                + "\n\n  To contribute: git clone the repository and run this from "
                "inside it."
                + "\n  For a first look only: add --measure-only."
            )
            return
        _fail(message)
        return

    if result.get("mode") == "measure":
        if as_json:
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            _print_measure_only(result)
        return

    if as_json:
        # stdout carries exactly one JSON document and nothing else — a caller
        # pipes it into `jq`. The artifact paths are already in `result["paths"]`.
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_report(result, verbose=True)

    if write:
        written = _write_artifacts(result, out or Path.cwd())
        if not as_json:
            _rule("Written")
            for label, path in written.items():
                typer.echo(f"  {label:<10}{path}")
            _print_next_steps(result, written)

    if not result["verification"]["ok"]:
        raise typer.Exit(code=1)


@app.command(name="check", help="Run the contribution review bar over an extractor file.")
def contribute_check(
    path: Path = typer.Argument(..., help="Path to an extractor.py."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw result as JSON."),
) -> None:
    """Check an existing extractor against the review bar.

    What it verifies: it still claims its own fixture, it does not accept any
    other family's fixture, a rejected binary leaves no stale evidence behind, it
    is reachable from the registry, and it declares the family it reports.
    """
    try:
        result = call("contribute_check", {"path": str(path)})
    except ApiError as exc:
        _fail(str(exc))
        return

    if as_json:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    typer.echo("")
    typer.echo(typer.style(f"  {path}", bold=True))
    for check in result["checks"]:
        mark = typer.style("  ✓", fg=typer.colors.GREEN) if check["ok"] else typer.style("  ✗", fg=typer.colors.RED)
        typer.echo(f"{mark} {check['name']}: {check['detail']}")
    typer.echo("")
    if not result["ok"]:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Presentation + file writing
# ---------------------------------------------------------------------------


def _identity_line(identity: dict) -> str:
    """One line for what the engine actually thinks the dump is.

    The most useful line in a measure-only report, and the one thing a `pip
    install` user can act on without a checkout: either "Unknown" (nobody knows
    this family — a contribution is worth making) or the family the engine
    already reports, in which case the change belongs in that extractor instead
    of in a new one.
    """
    if not identity.get("manufacturer"):
        return (
            "Unknown — no extractor matched, so the engine knows nothing about "
            "this family yet"
        )
    label = identity.get("ecu_variant") or identity.get("ecu_family") or "?"
    bits = [f"{identity['manufacturer']} {label}"]
    if identity.get("detection_strength"):
        bits.append(str(identity["detection_strength"]))
    for key, prefix in (("software_version", "sw"), ("hardware_number", "hw")):
        if identity.get(key):
            bits.append(f"{prefix}={identity[key]}")
    return " · ".join(bits)


def _print_measure_only(result: dict) -> None:
    """The pip-install report: measurement, limits, and nothing to act on yet."""
    analysis = result["analysis"]
    typer.echo("")
    typer.echo(
        typer.style(
            "  MEASUREMENT ONLY — no collision check, no marker recommended, "
            "nothing written.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
    )
    typer.echo(
        typer.style(
            "  This machine has no checkout of openremap-core "
            "(_dev/contrib/recipes/), which is what\n  the collision check needs. "
            "Everything below is local computation and stands.",
            dim=True,
        )
    )
    for entry in analysis["dumps"]:
        claim = entry.get("claimed_by")
        owner = (
            f"claimed by {claim['extractor']}"
            if claim
            else "not claimed by any extractor today"
        )
        typer.echo(f"  {entry['name']:<28}{entry['size']:>9,} bytes   {owner}")
        typer.echo(
            typer.style(f"      identify: {_identity_line(entry['identity'])}", dim=True)
        )

    if analysis["already_handled"]:
        typer.echo(
            typer.style(
                "  note: an extractor already claims this dump — the engine knows "
                "this family.",
                fg=typer.colors.GREEN,
            )
        )

    _rule("Runs stable across your dumps (unranked — not checked for collisions)")
    for candidate in analysis["candidates"][:8]:
        typer.echo(f"  {candidate['score']:>4}  {candidate['marker'][:52]!r}")

    if analysis["fields"]:
        _rule("Identity fields")
        for field in analysis["fields"][:6]:
            value = field["value"] or "(varies between your dumps)"
            typer.echo(
                f"  {field['kind']:<18}{str(value)[:34]:<36}{field['suggested_use']}"
            )

    if analysis["suggestions"]["from_descriptors"]:
        _rule("Family names your dump suggests")
        typer.echo("  " + ", ".join(analysis["suggestions"]["from_descriptors"]))

    for note in analysis.get("notes", []):
        typer.echo(typer.style(f"  note: {note}", fg=typer.colors.YELLOW))
    typer.echo("")


def _print_report(result: dict, *, verbose: bool) -> None:
    spec, analysis = result["spec"], result["analysis"]

    _rule(f"{spec['brand']} {spec['family']}")
    for entry in analysis["dumps"]:
        claim = entry.get("claimed_by")
        owner = (
            f"claimed by {claim['extractor']}"
            if claim
            else "not claimed by any extractor today"
        )
        typer.echo(f"  {entry['name']:<28}{entry['size']:>9,} bytes   {owner}")
        typer.echo(
            typer.style(f"      identify: {_identity_line(entry['identity'])}", dim=True)
        )

    if analysis["already_handled"]:
        typer.echo(
            typer.style(
                "  note: an extractor already claims this dump — if the identity it "
                "reports is wrong, the change belongs in that extractor, not in a new one.",
                fg=typer.colors.YELLOW,
            )
        )

    if not verbose:
        return

    _rule("Signature")
    typer.echo(f"  marker      {spec['marker']!r}")
    if spec["marker_offset"] is not None:
        typer.echo(f"  first at    0x{spec['marker_offset']:X}")
    typer.echo(f"  sizes       {', '.join(hex(s) for s in spec['sizes']) or '—'}")

    if not analysis.get("collisions_checked", True):
        _rule("Candidates (NOT checked against other families)")
        typer.echo(
            typer.style(
                "  No fixture recipes found — this machine has no checkout of\n"
                "  openremap-core (_dev/contrib/recipes/). Every marker below is\n"
                "  unranked by safety and none is recommended. Measurement stands;\n"
                "  the collision check and the generated files need a clone.",
                fg=typer.colors.YELLOW,
            )
        )
    else:
        _rule(f"Candidates (checked against {analysis['fixtures_checked']} fixtures)")
    for candidate in analysis["candidates"][:6]:
        verdict = candidate["verdict"]
        colour = typer.colors.GREEN if candidate["clean"] else typer.colors.YELLOW
        typer.echo(
            f"  {candidate['score']:>4}  {candidate['marker'][:44]!r:<48}"
            + typer.style(verdict[:64], fg=colour)
        )

    if analysis["fields"]:
        _rule("Identity fields")
        for field in analysis["fields"][:6]:
            value = field["value"] or "(varies between your dumps)"
            typer.echo(f"  {field['kind']:<18}{str(value)[:34]:<36}{field['suggested_use']}")

    for note in analysis.get("notes", []):
        typer.echo(typer.style(f"  note: {note}", fg=typer.colors.YELLOW))

    _rule("Review bar (the rendered extractor was executed to check this)")
    for check in result["verification"]["checks"]:
        mark = typer.style("  ✓", fg=typer.colors.GREEN) if check["ok"] else typer.style("  ✗", fg=typer.colors.RED)
        typer.echo(f"{mark} {check['name']}: {check['detail']}")


def _write_artifacts(result: dict, out_dir: Path) -> dict[str, str]:
    """Write the four artifacts. Returns label → path actually written."""
    paths = result["paths"]
    artifacts = result["artifacts"]
    written: dict[str, str] = {}

    recipe_path = Path(paths["recipe"])
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(
        json.dumps(artifacts["recipe"], indent=2) + "\n", encoding="utf-8"
    )
    written["fixture"] = str(recipe_path)

    extractor_path = Path(paths["extractor"])
    extractor_path.parent.mkdir(parents=True, exist_ok=True)
    init = extractor_path.parent / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    extractor_path.write_text(artifacts["extractor"], encoding="utf-8")
    written["extractor"] = str(extractor_path)

    test_path = Path(paths["test"])
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(artifacts["test"], encoding="utf-8")
    written["test"] = str(test_path)

    report_path = out_dir / paths["report"]
    report_path.write_text(artifacts["report"], encoding="utf-8")
    written["report"] = str(report_path)

    return written


def _print_next_steps(result: dict, written: dict[str, str]) -> None:
    spec = result["spec"]
    package_dir = result["paths"]["package"]
    class_name = result["artifacts"]["extractor"].split("class ", 1)[1].split("(", 1)[0]
    brand_dir = Path(package_dir).parent
    known_brand = (brand_dir / "__init__.py").is_file()
    _rule("Next steps")
    if not known_brand:
        typer.echo(
            f"  1. This is a NEW brand: create "
            f"{brand_dir}/__init__.py with an empty\n"
            "     EXTRACTORS list, and add the brand to BUILTIN_EXTRACTORS in\n"
            "     openremap/core/manufacturers/__init__.py (unpack it with *, like\n"
            "     the other brands) BEFORE registering the extractor itself."
        )
    typer.echo(
        f"  {'2' if not known_brand else '1'}. Register the extractor — the decision "
        "only a human can make, because\n"
        "     position IS behaviour (first match wins). Paste into the brand's\n"
        f"     {brand_dir}/__init__.py:"
    )
    typer.echo(
        typer.style(
            f"       from {package_dir.replace('/', '.')}.extractor "
            f"import {class_name}\n"
            f"       EXTRACTORS = [\n"
            f"           ...,  # families it must sit after\n"
            f"           {class_name}(),  # <why this position: what it could overlap>\n"
            f"       ]",
            dim=True,
        )
    )
    step = "3" if not known_brand else "2"
    typer.echo(f"  {step}. Rebuild the corpus and re-run the review bar:")
    typer.echo(
        typer.style(
            "       .venv/bin/python _dev/contrib/build_corpus.py build\n"
            f"       openremap contribute check {written['extractor']}\n"
            f"       .venv/bin/python -m pytest {written['test']}",
            dim=True,
        )
    )
    step = "4" if not known_brand else "3"
    typer.echo(
        f"  {step}. Fill in the TODOs in the extractor (identity fields) and the\n"
        "     'Not verified' section of the report before opening the PR."
    )
    _rule("What to commit — and what never to")
    typer.echo(
        "  commit: the recipe, the .mimic the corpus build writes, the manifest\n"
        "          entry, the extractor (+ __init__.py files), the registry line,\n"
        "          the test\n"
        "  NEVER:  your dumps. Only the few hundred bytes of the recipe travel.\n"
        "          `*.bin` is gitignored, but a dump renamed to .ori or .dump is\n"
        "          not — check `git status` before you `git add`."
    )
