#!/usr/bin/env python3
"""
Build the wizard's mimic corpus (dev tool, committed).

**What this is for:** exactly one consumer — the contribution wizard, which
needs files that *real extractors accept* so it can tell a contributor "your
candidate signature also fires on a file belonging to another family". It is
**not** a test corpus, it is **not** wired into pytest, and it proves nothing
about real ECUs: every file is built from a recipe we wrote by reading our own
extractors. Treat it as a fixture, never as evidence.

**How it works:** each recipe (`recipes/<manufacturer>-<family>.json`) describes
a file as *fill byte + planted byte sequences at positions*, plus what
`identify` must report for it. The tool rebuilds every file deterministically,
verifies the identity, and writes the corpus plus a manifest of hashes.

**Tamper detection** (the reason `--check` exists): the manifest records the
sha256 of every generated file *and* of every recipe. A contributor who edits a
corpus file by hand — say, to make their candidate signature look unique — must
also edit the manifest and the script, and *that* shows up in a review diff
instead of a binary blob nobody can read.

Usage:
    .venv/bin/python _dev/contrib/build_corpus.py verify          # recipes still identify as expected
    .venv/bin/python _dev/contrib/build_corpus.py build           # (re)write corpus/ + manifest.json
    .venv/bin/python _dev/contrib/build_corpus.py build --check   # write nothing; fail on any drift
    .venv/bin/python _dev/contrib/build_corpus.py search EDC15    # dev aid: brute-force a recipe
    .venv/bin/python _dev/contrib/build_corpus.py coverage        # what is covered, measured
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openremap.core.services.identify.identifier import identify_ecu  # noqa: E402

CONTRIB_DIR = Path(__file__).resolve().parent
RECIPE_DIR = CONTRIB_DIR / "recipes"
CORPUS_DIR = CONTRIB_DIR / "corpus"
MANIFEST = CORPUS_DIR / "manifest.json"


# ---------------------------------------------------------------------------
# Recipe → bytes
# ---------------------------------------------------------------------------


def _position(expr: str, size: int) -> int:
    """Resolve a position expression: ``0x7FD9`` or ``end-0x40``."""
    expr = expr.strip()
    if expr.startswith("end-"):
        return size - int(expr[4:], 16)
    return int(expr, 16)


def _token_bytes(token: dict) -> bytes:
    if "text" in token:
        return token["text"].encode("ascii")
    if "hex" in token:
        return bytes.fromhex(token["hex"].replace(" ", ""))
    if "run" in token:
        return bytes([int(token["run"], 16)]) * int(token["count"])
    raise ValueError(f"unknown token: {token!r}")


def build_file(recipe: dict) -> bytes:
    size = int(recipe["file_size"])
    fill = bytes.fromhex(recipe.get("fill", "FF"))
    if len(fill) != 1:
        raise ValueError(f"{recipe['name']}: fill must be one byte")
    buf = bytearray(fill * size)

    for plant in recipe.get("plants", []):
        pos = _position(plant["at"], size)
        for token in plant["seq"]:
            payload = _token_bytes(token)
            if pos + len(payload) > size:
                raise ValueError(
                    f"{recipe.get('name', '<candidate>')}: plant at {plant['at']} runs past EOF"
                )
            buf[pos:pos + len(payload)] = payload
            pos += len(payload)

    return bytes(buf)


def file_name(recipe: dict) -> str:
    """Corpus file name — derived from the *recipe* name, which is unique.

    Not ``manufacturer-family``: several recipes legitimately describe the same
    reported family. The EDC16 extractor hardcodes ``ecu_family="EDC16"`` and
    carries the C31/C34/CP33 distinction in ``ecu_variant``, so ten different
    family-level recipes all report "EDC16" — and a name built from the reported
    family silently made nine of them overwrite the tenth.

    Extension is ``.mimic``, **not** ``.bin``: the repository gitignores
    ``*.bin`` (no ECU firmware, ever), so a ``.bin`` fixture would silently
    never be committed — and it would also make the "no binaries in this repo"
    rule read as a lie. Detection does not depend on the extension
    (``can_handle()`` only sees bytes), so nothing is lost.
    """
    stem = recipe.get("name")
    if stem:
        stem = Path(stem).stem.lower()
    else:
        stem = f"{recipe['manufacturer']}-{recipe['ecu_family']}".lower().replace(" ", "-")
    return f"{stem}-{int(recipe['file_size']) // 1024}k.mimic"


def load_recipes() -> list[dict]:
    out = []
    for path in sorted(RECIPE_DIR.glob("*.json")):
        recipe = json.loads(path.read_text(encoding="utf-8"))
        recipe["name"] = path.name
        recipe["_path"] = path
        out.append(recipe)
    return out


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def check_identity(recipe: dict, data: bytes) -> str | None:
    """Return an error string if *data* does not identify as the recipe says.

    The gate is **manufacturer + ecu_family**: that is what the wizard needs
    ("does my candidate signature also fire on a file of another family?").
    ``ecu_variant`` is also gated **when the recipe declares it** — the family
    level alone is too coarse for the extractors that carry the whole
    distinction in the variant (Bosch EDC16 hardcodes ``ecu_family="EDC16"`` and
    reports C31/C34/CP33 as variants), so a fixture for one of those variants
    must pin the variant explicitly. ``match_key`` is only verified when a
    recipe opts in — reproducing the extracted *fields* would mean rebuilding
    each family's real ident-block structure, which buys the wizard nothing.
    """
    result = identify_ecu(data, file_name(recipe))
    expect = recipe["expect"]
    for key in ("manufacturer", "ecu_family", "ecu_variant"):
        if key not in expect:
            continue
        want, got = expect.get(key), result.get(key)
        if want != got:
            return f"{key}: expected {want!r}, got {got!r}"
    if "match_key" in expect and expect["match_key"] != result.get("match_key"):
        return f"match_key: expected {expect['match_key']!r}, got {result.get('match_key')!r}"
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_verify(_args) -> int:
    recipes = load_recipes()
    if not recipes:
        print("no recipes found", file=sys.stderr)
        return 1
    bad = 0
    for recipe in recipes:
        error = check_identity(recipe, build_file(recipe))
        if error:
            bad += 1
            print(f"FAIL {file_name(recipe)}: {error}")
        else:
            print(f"  ok {file_name(recipe):34} → {recipe['expect']['ecu_family']}")
    print(f"\n{len(recipes) - bad}/{len(recipes)} recipes verify")
    return 1 if bad else 0


def cmd_build(args) -> int:
    recipes = load_recipes()
    entries, drift = [], 0
    previous = {}
    if MANIFEST.is_file():
        previous = {
            e["name"]: e for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
        }

    for recipe in recipes:
        data = build_file(recipe)
        error = check_identity(recipe, data)
        name = file_name(recipe)
        if error:
            print(f"FAIL {name}: {error}", file=sys.stderr)
            return 1

        recipe_sha = _sha256(recipe["_path"].read_bytes())
        observed = identify_ecu(data, name)
        entry = {
            "name": name,
            "sha256": _sha256(data),
            "bytes": len(data),
            "recipe": recipe["name"],
            "recipe_sha256": recipe_sha,
            "expect": recipe["expect"],
            # Informational: what the mimic actually yields. Fields are NOT
            # reproduced faithfully (see check_identity) — do not gate on these.
            "observed": {
                "manufacturer": observed.get("manufacturer"),
                "ecu_family": observed.get("ecu_family"),
                "match_key": observed.get("match_key"),
            },
        }
        entries.append(entry)

        old = previous.get(name)
        if old and (old["sha256"] != entry["sha256"] or old["recipe_sha256"] != recipe_sha):
            drift += 1
            print(f"DRIFT {name}: corpus or recipe changed")

        if not args.check:
            CORPUS_DIR.mkdir(parents=True, exist_ok=True)
            (CORPUS_DIR / name).write_bytes(data)

    if args.check:
        problems = []
        for entry in entries:
            on_disk = CORPUS_DIR / entry["name"]
            if not on_disk.is_file():
                problems.append(f"missing from corpus/: {entry['name']}")
                continue
            # The whole point of --check: a hand-edited corpus file must not
            # slip through. Comparing the manifest against freshly-built bytes
            # only catches recipe edits, not a tampered .bin.
            actual = _sha256(on_disk.read_bytes())
            if actual != entry["sha256"]:
                problems.append(
                    f"corpus file modified: {entry['name']} "
                    f"(on disk {actual[:16]}… ≠ manifest {entry['sha256'][:16]}…)"
                )

        if previous:
            known = set(previous)  # dict: name -> entry
            problems += [f"new recipe not in manifest: {n}" for n in
                         ({e["name"] for e in entries} - known)]
            problems += [f"recipe gone but still in manifest: {n}" for n in
                         (known - {e["name"] for e in entries})]

        if problems or drift:
            print("--check failed:", file=sys.stderr)
            for p in problems:
                print(f"  ✗ {p}", file=sys.stderr)
            if drift:
                print(f"  ✗ {drift} recipe(s) changed (rebuild needed)", file=sys.stderr)
            print(
                "\nIf the change is intentional, run without --check and review "
                "the diff. If you did not change anything, the corpus has been "
                "edited by hand — restore it from the recipes.",
                file=sys.stderr,
            )
            return 1
        print(f"in sync: {len(entries)} files")
        return 0

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale corpus files (e.g. left behind by a renamed recipe), so
    # `git status` cannot show a fixture the manifest does not describe.
    keep = {e["name"] for e in entries}
    for stale in CORPUS_DIR.glob("*.mimic"):
        if stale.name not in keep:
            print(f"  removing stale {stale.name}")
            stale.unlink()
    MANIFEST.write_text(
        json.dumps(
            {
                "schema": 1,
                "purpose": "wizard-only fixture corpus — not evidence about real ECUs",
                "generated_by": "_dev/contrib/build_corpus.py",
                "files": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    total = sum(e["bytes"] for e in entries)
    print(f"wrote {len(entries)} files ({total / 1024 / 1024:.1f} MB raw) + manifest.json")
    return 0


def cmd_coverage(_args) -> int:
    """Report what the corpus actually covers — measured, not asserted.

    Two numbers matter to the wizard:

    * **extractors covered** — some fixture's ``can_handle()`` fires on the real
      extractor, which is also the ordering check (an earlier extractor stealing
      a fixture shows up here as the wrong claimer).
    * **family keys covered** — ``identify`` reports the key as ``ecu_family``
      *or* ``ecu_variant``, because several extractors carry the whole family
      distinction in the variant (Bosch EDC16 reports ``ecu_family="EDC16"``
      for ten different C-numbers).

    Declared keys with no fixture are printed with the extractors that declare
    them, and marked when not even one real dump in the snapshot produces them —
    those are the keys no fixture can ever be validated against.
    """
    from openremap.core.manufacturers import get_extractors

    recipes = load_recipes()
    extractors = get_extractors()
    modules = [type(e).__module__ for e in extractors]

    claimed: set[str] = set()
    produced: dict[str, str] = {}
    family_values: dict[str, str] = {}
    for recipe in recipes:
        data = build_file(recipe)
        name = file_name(recipe)
        for extractor, module in zip(extractors, modules):
            if extractor.can_handle(data):
                claimed.add(module)
                break
        result = identify_ecu(data, name)
        for key in ("ecu_family", "ecu_variant"):
            value = result.get(key)
            if value:
                produced.setdefault(value, name)
        if result.get("ecu_family"):
            family_values.setdefault(result["ecu_family"], name)

    declared: dict[str, list[str]] = {}
    for module, extractor in zip(modules, extractors):
        for family in extractor.supported_families or []:
            declared.setdefault(family, []).append(module)

    def short(module: str) -> str:
        return module.replace("openremap.core.manufacturers.", "").replace(".extractor", "")

    real: set[str] = set()
    snapshot = ROOT / "_dev/oracle/smallfixes-snapshot/after/identify.jsonl"
    if snapshot.is_file():
        for line in snapshot.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for key in ("ecu_family", "ecu_variant"):
                if row.get(key):
                    real.add(row[key])

    missing_extractors = sorted(short(m) for m in modules if m not in claimed)
    print(f"extractors:  {len(modules) - len(missing_extractors)}/{len(modules)} covered by a fixture")
    for module in missing_extractors:
        print(f"   ✗ {module}")

    covered = sorted(set(declared) & set(produced))
    uncovered = sorted(set(declared) - set(produced))
    print(f"\nfamily keys: {len(covered)}/{len(declared)} declared keys are produced by a fixture")
    for family in uncovered:
        where = ", ".join(short(m) for m in declared[family])
        note = "" if family in real else "   ← no real dump produces it either"
        print(f"   ✗ {family:14} declared by {where}{note}")

    undeclared = sorted(set(family_values) - set(declared))
    if undeclared:
        print("\nreported as ecu_family but declared by no extractor (fixture smells wrong):")
        for family in undeclared:
            print(f"   ! {family:14} from {family_values[family]}")
    return 0


def cmd_search(args) -> int:
    """Brute-force a recipe for one family — a dev aid, not part of the build."""
    import itertools

    family = args.family
    rng = args.limit
    for m, f in (("Bosch", family), ("Denso", family), ("Siemens", family),
                 ("Marelli", family), ("Delphi", family), ("Hitachi", family)):
        pass  # manufacturer only matters for the recipe header

    rows = [
        json.loads(line)
        for line in Path(args.snapshot).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row = next(
        (r for r in rows if r.get("ecu_family") == family and r.get("raw_strings")), None
    )
    if row is None:
        print(f"no snapshot row with raw_strings for {family!r}", file=sys.stderr)
        return 1

    def offsets(size: int) -> dict:
        return {
            "0x7fd9": 0x7FD9, "0x8000": 0x8000, "0x10000": 0x10000,
            "0x2000": 0x2000, "end-0x40": size - 0x40, "end-0x100": size - 0x100,
            "mid": size // 2,
        }

    strings = [s.encode("ascii", "ignore") for s in row["raw_strings"] if s]
    found = None
    for fill, sep, (oname, off), rev in itertools.product(
        (0xFF, 0xC3, 0x00),
        (b"\xff", b".", b" ", b"\x00", b"\n"),
        offsets(row["file_size"]).items(),
        (False, True),
    ):
        seq, pos = (strings[::-1] if rev else strings), off
        buf = bytearray(bytes([fill]) * row["file_size"])
        ok = True
        for s in seq:
            if pos + len(s) > len(buf):
                ok = False
                break
            buf[pos:pos + len(s)] = s
            pos += len(s) + len(sep)
        if not ok:
            continue
        if identify_ecu(bytes(buf), "mimic.bin").get("ecu_family") == family:
            found = {"fill": f"{fill:02X}", "separator": sep.hex(), "at": oname,
                     "reverse": rev, "size": row["file_size"]}
            break

    if not found:
        print(f"{family}: no parameter combination in the search space worked", file=sys.stderr)
        return 1
    print(json.dumps({"family": family, "found": found,
                      "source_sha256": row["sha256"],
                      "strings": row["raw_strings"][:rng]}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="check each recipe still identifies as expected")
    sub.add_parser("coverage", help="report extractor + family-key coverage")

    p_build = sub.add_parser("build", help="write corpus/ + manifest.json")
    p_build.add_argument("--check", action="store_true", help="write nothing; fail on drift")

    p_search = sub.add_parser("search", help="dev aid: brute-force a recipe")
    p_search.add_argument("family")
    p_search.add_argument("--limit", type=int, default=8)
    p_search.add_argument(
        "--snapshot",
        default=str(ROOT / "_dev/oracle/smallfixes-snapshot/after/identify.jsonl"),
    )

    args = ap.parse_args()
    return {
        "verify": cmd_verify,
        "coverage": cmd_coverage,
        "build": cmd_build,
        "search": cmd_search,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
