#!/usr/bin/env python3
"""
Derive *family-level* mimic recipes from the per-extractor ones (dev tool).

One recipe per extractor is not family coverage: `bosch.me7` declares 12 family
keys and `bosch.edc16` thirteen, and the wizard needs a fixture for each key a
new extractor could collide with. This tool takes the working per-extractor
recipe and, for every family that extractor declares, tries to steer the file to
that family by substituting the family token into the planted ident strings —
then verifies `identify` actually reports it (as `ecu_family` **or**
`ecu_variant`, because some extractors carry the distinction in the variant).

Family keys that never appear in any real file are reported separately: a mimic
cannot be validated against a family the engine has never produced.

Usage:
    .venv/bin/python _dev/contrib/derive_families.py            # report only
    .venv/bin/python _dev/contrib/derive_families.py --write    # write recipes
"""

from __future__ import annotations

import argparse
import codecs
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openremap.core.manufacturers import get_extractors  # noqa: E402
from openremap.core.services.identify.identifier import identify_ecu  # noqa: E402
from _dev.contrib.build_corpus import build_file  # noqa: E402

RECIPE_DIR = Path(__file__).resolve().parent / "recipes"
SNAPSHOT = ROOT / "_dev/oracle/smallfixes-snapshot/after/identify.jsonl"
TOKEN = re.compile(r"[A-Z]{2,}[0-9][A-Z0-9+.\-]*")

# extractor module -> recipe file name, filled by :func:`base_recipes`
BASE_FILES: dict[str, str] = {}


def claimed_by(data: bytes) -> str | None:
    """Module of the extractor that actually claims *data* (first ``can_handle``).

    A fixture is only a fixture for the extractor that wins: the ``ME7910``
    marker planted into a minimal recipe also contains ``ME7.``, so the ME7
    extractor — earlier in the registry — claimed the file and *its* loose
    ``ME\\d+`` family regex happily reported ``ME7910``. The key looked covered
    while the ME7.9.10 extractor had never seen the file.
    """
    for extractor in get_extractors():
        if extractor.can_handle(data):
            return type(extractor).__module__
    return None


def declared_families() -> dict[str, list[str]]:
    """family key -> list of extractor module names declaring it."""
    out: dict[str, list[str]] = {}
    for extractor in get_extractors():
        for family in extractor.supported_families or []:
            out.setdefault(family, []).append(type(extractor).__module__)
    return out


def observed_families() -> set[str]:
    seen: set[str] = set()
    if not SNAPSHOT.is_file():
        return seen
    for line in SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for key in ("ecu_family", "ecu_variant"):
            if row.get(key):
                seen.add(row[key])
    return seen


def base_recipes() -> dict[str, dict]:
    """extractor module -> its working recipe (the one already committed).

    Falls back to resolving the module from the registry when a recipe does not
    name it (the hand-authored ones record a note instead of a module path).
    """
    by_family: dict[str, str] = {}
    by_module: dict[str, str] = {}
    for extractor in get_extractors():
        module = type(extractor).__module__
        by_module[module] = module
        for family in extractor.supported_families or []:
            by_family.setdefault(family, module)

    out: dict[str, dict] = {}
    for path in sorted(RECIPE_DIR.glob("*.json")):
        recipe = json.loads(path.read_text(encoding="utf-8"))
        source = recipe.get("derived_from", {}).get("extractor")
        if not source:
            source = by_family.get(recipe["expect"]["ecu_family"])
        if source and source not in out:
            out[source] = recipe
            BASE_FILES[source] = path.name
    return out


def _module_sources(module_name: str) -> list[tuple[str, str]]:
    """(label, source text) of an extractor module plus its ``patterns.py``."""
    import importlib

    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return []
    sources = []
    module_file = getattr(mod, "__file__", None)
    if module_file:
        sources.append(("extractor.py", Path(module_file).read_text(encoding="utf-8")))
        patterns_file = Path(module_file).with_name("patterns.py")
        if patterns_file.is_file():
            sources.append(("patterns.py", patterns_file.read_text(encoding="utf-8")))
    return sources


# ``data[0] == 0x22`` / ``data[1:5] == b"\xff\xff\xff\xff"`` / ``data[:2] == b"\x02\x02"``
_BYTE_EQ = re.compile(r"data\[\s*(\d+)\s*\]\s*==\s*(0x[0-9A-Fa-f]{1,2})")
_SLICE_EQ = re.compile(
    r"""data\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*==\s*(?:rb|br|b|r)?['"]([^'"]*)['"]"""
)
_PREFIX_EQ = re.compile(r"""data\[\s*:\s*(\d+)\s*\]\s*==\s*(?:rb|br|b|r)?['"]([^'"]*)['"]""")
_SIZE_EQ = re.compile(
    r"\b(?:sz|size|len\(\s*data\s*\))\s*==\s*(0x[0-9A-Fa-f]+|\d+|[A-Z_][A-Z0-9_]*)"
)
_IF_LINE = re.compile(r"^(\s*)(?:if|elif)\s")

# A quoted literal, in either quote style, with an optional bytes/regex prefix.
# The content deliberately allows the *other* quote character: ``rb'"0000000M2\.(\d)'``
# is the m2x family marker, and a ``[^'"]`` guard would skip it.
_QUOTED = re.compile(r"""(?:rb|br|b|r)?(['\"])([^'\n]*?)\1""")


def _literals(text: str, limit: int | None = None) -> list[str]:
    """String/bytes literal bodies in a module's source (quote-style aware)."""
    found = [content for _quote, content in _QUOTED.findall(text)]
    return found[:limit] if limit else found


def _const_int(token: str, module_name: str) -> int | None:
    """Resolve ``0x8000``, ``262144`` or a module constant such as ``_MAX_SIZE``."""
    if token.startswith("0x"):
        return int(token, 16)
    if token.isdigit():
        return int(token)
    import importlib

    try:
        value = getattr(importlib.import_module(module_name), token, None)
    except Exception:
        return None
    return value if isinstance(value, int) else None


def _if_blocks(text: str) -> list[str]:
    """Split source into one chunk per ``if``/``elif`` statement.

    The legacy Bosch extractor decides its family in a chain of separate ``if``
    statements (``A — DME-3.2``, ``B — M1.x-early`` …), and constraints from
    different branches are mutually exclusive: ``data[0]`` cannot be ``0x22``
    *and* ``0x02``. Grouping by statement is what makes the harvest usable —
    a blind combination of all constraints mostly produces impossible files.
    """
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        match = _IF_LINE.match(lines[index])
        if not match:
            index += 1
            continue
        indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                break
            end += 1
        blocks.append("\n".join(lines[index:end]))
        index = end
    return blocks


def _conditions_in(text: str) -> list[tuple[int, bytes]]:
    found: list[tuple[int, bytes]] = []
    for offset, value in _BYTE_EQ.findall(text):
        found.append((int(offset), bytes([int(value, 16)])))
    for start, end, literal in _SLICE_EQ.findall(text):
        payload = _decode_literal(literal)
        if payload and int(end) - int(start) == len(payload):
            found.append((int(start), payload))
    for end, literal in _PREFIX_EQ.findall(text):
        payload = _decode_literal(literal)
        if payload and int(end) == len(payload):
            found.append((0, payload))
    return found


def _decode_literal(literal: str) -> bytes:
    try:
        payload, _ = codecs.escape_decode(literal.encode("utf-8"))
    except Exception:
        return b""
    return payload


def header_groups(module_name: str) -> list[list[tuple[int, bytes]]]:
    """Byte constraints the extractor tests on the ROM header, grouped by branch."""
    groups: list[list[tuple[int, bytes]]] = []
    for _label, text in _module_sources(module_name):
        for block in _if_blocks(text):
            conditions = _conditions_in(block)
            if conditions:
                groups.append(conditions)
    return groups


def header_sizes(module_name: str) -> list[int]:
    """Exact file sizes the extractor demands (``sz == _MAX_SIZE``, ``0x8000``).

    File size is itself a family discriminator: the Siemens SIMOS extractor
    returns ``SIMOS2`` for a 262 KB dump and ``SIMOS3`` for a 524 KB one and
    ``SIMOS`` for 131 KB, with no family string involved at all.
    """
    sizes: set[int] = set()
    for _label, text in _module_sources(module_name):
        for token in _SIZE_EQ.findall(text):
            value = _const_int(token, module_name)
            if value:
                sizes.add(value)
    return sorted(sizes)


def _compatible(chosen: list[tuple[int, bytes]], other: tuple[int, bytes]) -> bool:
    start, payload = other
    end = start + len(payload)
    return not any(start < s + len(p) and s < end for s, p in chosen)


def header_variants(recipe: dict, module_name: str, limit: int = 300) -> list[dict]:
    """Candidate recipes that plant the extractor's header magic bytes.

    Three of the legacy Bosch families are decided by *magic bytes*, not by an
    ident string: ``DME-3.2`` needs ``22 FF FF FF FF 02`` at offset 0 and
    ``EZK`` needs ``81 5C`` in a file of exactly 32 KB. Substituting a family
    token into a string cannot reach those, so the extractor's own byte
    comparisons are harvested and planted instead.
    """
    groups = header_groups(module_name)
    sizes = header_sizes(module_name)
    if not groups and not sizes:
        return []

    combos: list[list[tuple[int, bytes]]] = []
    for group in groups:
        chosen: list[tuple[int, bytes]] = []
        for condition in group:
            if _compatible(chosen, condition):
                chosen.append(condition)
        if chosen:
            combos.append(chosen)
        combos.extend([[c] for c in group])
    if not combos:
        combos = [[]]  # size-only discriminator: change nothing but file_size

    seen: set[str] = set()
    out: list[dict] = []
    for combo in combos:
        for size in sizes or [None]:
            # Two shapes, minimal first: a header-decided family needs no ident
            # payload at all, and keeping the base recipe's plants actively
            # hurts — the KE-Jetronic base plants its "028080…" trailer, and the
            # extractor dispatches on that trailer *before* looking at the
            # header, so the base recipe can never report DME-3.2/EZK.
            shapes = [
                {
                    key: json.loads(json.dumps(value))
                    for key, value in recipe.items()
                    if key in ("manufacturer", "file_size", "fill")
                },
                json.loads(json.dumps(recipe)),
            ]
            for copy in shapes:
                if size:
                    copy["file_size"] = size
                copy["plants"] = list(copy.get("plants", []))
                for offset, payload in combo:
                    copy["plants"].append(
                        {"at": f"0x{offset:x}", "seq": [{"hex": payload.hex()}]}
                    )
                # Key on the *whole* plant list, not its tail: the minimal shape
                # (no plants at all) and the base-derived shape otherwise collide
                # and the second one is silently dropped — which is what made
                # every size-only family impossible to steer.
                key = json.dumps([copy["file_size"], copy["plants"]])
                if key in seen:
                    continue
                seen.add(key)
                out.append(copy)
                if len(out) >= limit:
                    return out
    return out


def marker_variants(recipe: dict, target: str, module_name: str) -> list[dict]:
    """Candidates built from the *family markers the extractor itself carries*.

    Many extractors decide the family from a marker string rather than from a
    token in the recipe we already have: ``FAMILY_MARKERS`` maps
    ``b'"0000000M1.7'`` to ``'M1.7'``, and ``m2x`` regexes
    ``rb'"0000000M2\\.(\\d)'``. Harvesting those gives the exact bytes each
    family needs, and substituting the family token into a marker of the same
    shape covers the ones with no exact entry.
    """
    import importlib

    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return []

    markers: list[bytes] = []
    token = re.compile(rb"[A-Z]{2,}[0-9][A-Z0-9.\-]*")

    # dicts that map a marker to a family name (FAMILY_MARKERS and friends)
    for value in vars(mod).values():
        if isinstance(value, dict):
            for key, mapped in value.items():
                if isinstance(key, bytes):
                    if isinstance(mapped, str) and mapped == target:
                        markers.insert(0, key)          # exact: best possible
                    else:
                        markers.append(key)
        elif isinstance(value, bytes) and 4 <= len(value) <= 40:
            markers.append(value)

    for _label, text in _module_sources(module_name):
        literals = _literals(text)
        for literal in literals[:1000]:
            # Only real byte literals: a regex escape like \\d would decode to
            # a literal "d" and plant nonsense.
            if re.search(r"\\[^x0-9]", literal):
                continue
            try:
                decoded, _ = codecs.escape_decode(literal.encode("utf-8"))
            except Exception:
                continue
            if 4 <= len(decoded) <= 40 and decoded not in markers:
                markers.append(decoded)

    # Regex literals are patterns, not text, so the escape filter above drops
    # them — but they are exactly what the number-pattern extractors test for:
    # m2x looks for ``rb'"0000000M2\.(\d)'``, where the digit is the family.
    # Keeping them lets an instance be built for the target family's digits.
    # No pre-truncation: the marker sits deep in the file, past the docstrings
    # that dominate a "first N literals" cut, and a missed marker here is a
    # family that can never be steered.
    regex_markers: list[bytes] = []
    for _label, text in _module_sources(module_name):
        for literal in _literals(text):
            if not re.search(r"\\d|\[0-9\]|\\w", literal):
                continue
            decoded = _decode_literal(literal)
            if 2 <= len(decoded) <= 60 and decoded not in regex_markers:
                regex_markers.append(decoded)
    regex_markers = regex_markers[:120]

    exact: list[bytes] = [target.encode()]
    for marker in markers:
        if target.encode() in marker:
            exact.append(marker)

    # A regex marker: ``M2\.(\d)`` → one instance per digit in the target name
    # ("M2.81" yields instances for 2, 8 and 1, and the extractor keeps the one
    # that matches its own branch). Instances of the *regex* markers come first:
    # they are the extractor's actual family decision, whereas the plain
    # literals are mostly sample strings quoted in docstrings, and the cap below
    # would otherwise be spent before reaching them.
    loose: list[bytes] = []
    for marker in regex_markers + markers:
        for d in dict.fromkeys(re.findall(r"\d", target)):
            inst = marker.replace(rb"\\.", b".").replace(rb"\.", b".").replace(rb"\d", d.encode())
            inst = re.sub(rb"\[0-9\]", d.encode(), inst)
            inst = re.sub(rb"\\w", b"9", inst)
            inst = re.sub(rb"[()?]", b"", inst)
            if 2 <= len(inst) <= 48:
                loose.append(inst)

    # A marker-shaped token inside a longer literal (``…/M3.1/…``) can also be
    # swapped for the target family.
    for marker in markers:
        found = token.search(marker)
        if found:
            loose.append(marker[:found.start()] + target.encode() + marker[found.end():])

    # Bounded: a wide literal harvest can produce hundreds of candidates.
    exact_set = set(exact)
    cands = list(dict.fromkeys(c for c in exact + loose if 2 <= len(c) <= 48))
    cands = [c for c in cands if c in exact_set] + [
        c for c in cands if c not in exact_set
    ][:250]

    anchors = {pl["at"] for pl in recipe["plants"]}
    for extra in ("0x0", "0x1000", "0x7f00", "0x7fd9", "0x8000", "0x10000"):
        anchors.add(extra)

    out: list[dict] = []
    for candidate in cands:
        for anchor in sorted(anchors):
            copy = json.loads(json.dumps(recipe))
            copy["plants"].append({"at": anchor, "seq": [{"hex": candidate.hex()}]})
            out.append(copy)
    return out


def marker_literals(module_name: str, limit: int = 30) -> list[bytes]:
    """Short printable literals the extractor compares against, digits first.

    These are the extractor's own family markers (``1350000M3``, ``MP7.2``,
    ``0000000M3``). They matter because some families are chosen by *marker
    precedence*: ``m3x`` returns ``M3.1`` whenever ``1350000M3`` is present, so
    a fixture for ``MP3.2`` must not carry the M3.1 marker at all.
    """
    found: list[bytes] = []
    for _label, text in _module_sources(module_name):
        for literal in _literals(text):
            if re.search(r"\\[^x0-9]", literal):
                continue
            payload = _decode_literal(literal)
            if 4 <= len(payload) <= 32 and re.fullmatch(rb"[\x20-\x7e]+", payload):
                found.append(payload)
    unique = list(dict.fromkeys(found))
    with_digit = [p for p in unique if re.search(rb"\d", p)]
    return (with_digit + [p for p in unique if p not in with_digit])[:limit]


def minimal_variants(
    recipe: dict, target: str, module_name: str, limit: int = 240
) -> list[dict]:
    """Bare recipes: fill byte, file size, and the target marker — nothing else.

    ``variants()`` keeps the base recipe's plants and edits one of them, which
    cannot reach a family that is chosen by *marker precedence*: the ``m3x``
    base plants ``1350000M3`` (always ``M3.1``), so no edit of that plant ever
    produces ``MP3.2``. Dropping the base's plants and planting the target
    beside each of the extractor's own markers can, because a marker pair like
    ``0000000M3`` + ``MP3.2`` is exactly what the extractor looks for.
    """
    anchors = sorted({pl["at"] for pl in recipe.get("plants", [])} | {
        "0x0", "0x1000", "0x2000", "0x4000", "0x8000", "0x10000",
    })
    markers = marker_literals(module_name, limit=12)

    payloads: list[bytes] = [target.encode()]
    for marker in markers:
        payloads.append(marker + target.encode())
        payloads.append(target.encode() + marker)

    base = {
        key: json.loads(json.dumps(value))
        for key, value in recipe.items()
        if key in ("manufacturer", "file_size", "fill")
    }

    out: list[dict] = []
    for payload in payloads:
        for anchor in anchors:
            copy = json.loads(json.dumps(base))
            copy["plants"] = [{"at": anchor, "seq": [{"hex": payload.hex()}]}]
            out.append(copy)
            if len(out) >= limit:
                return out
    return out


def variants(recipe: dict, target: str, base_family: str) -> list[dict]:
    """Candidate recipes that try to steer the fixture to *target*."""
    out: list[dict] = []
    token = target.encode()

    for index, plant in enumerate(recipe["plants"]):
        for token_index, item in enumerate(plant["seq"]):
            # Recipes store payloads as hex, so render them to ASCII first:
            # substituting only in "text" tokens missed every generated recipe.
            if "hex" in item:
                try:
                    text = bytes.fromhex(item["hex"]).decode("ascii")
                except (ValueError, UnicodeDecodeError):
                    continue
            else:
                text = item.get("text", "")
            replaced = text.replace(base_family, target)
            if replaced == text:
                # The plant may hold a *different* family-shaped token (e.g. the
                # ident carries "ME7.1" while the recipe's family is a variant).
                replaced = TOKEN.sub(target, text, count=1)
            if replaced != text:
                copy = json.loads(json.dumps(recipe))
                copy["plants"][index]["seq"][token_index] = (
                    {"text": replaced} if "text" in item
                    else {"hex": replaced.encode("ascii", "ignore").hex()}
                )
                out.append(copy)

        # Also try planting the target *in addition*, right after the plant.
        copy = json.loads(json.dumps(recipe))
        copy["plants"].insert(index + 1, {"at": plant["at"], "seq": [{"hex": token.hex()}]})
        out.append(copy)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--write", action="store_true")
    ap.add_argument(
        "--include-unobserved",
        action="store_true",
        help="also attempt declared family keys that never appear in a real file",
    )
    ap.add_argument("--report", metavar="PATH", help="write a full JSON report here")
    ap.add_argument("--only", metavar="FAMILY[,FAMILY…]", help="restrict to these families")
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="re-pin expect (family + variant) of the family recipes already written",
    )
    args = ap.parse_args()
    only = {f.strip() for f in args.only.split(",")} if args.only else None

    bases = base_recipes()
    declared = declared_families()
    observed = observed_families()

    covered = {
        json.loads(p.read_text(encoding="utf-8"))["expect"]["ecu_family"]
        for p in RECIPE_DIR.glob("*.json")
    }

    written, failed, unreachable = [], [], []
    unobserved_hits: list[str] = []
    failed_probe: dict[str, dict] = {}

    if args.refresh:
        refreshed = 0
        for path in sorted(RECIPE_DIR.glob("*.json")):
            recipe = json.loads(path.read_text(encoding="utf-8"))
            if "family_target" not in recipe.get("derived_from", {}):
                continue
            try:
                pinned = identify_ecu(build_file(recipe), path.name)
            except ValueError as error:
                print(f"  ! {path.name}: {error}")
                continue
            recipe["expect"] = {
                "manufacturer": pinned.get("manufacturer"),
                "ecu_family": pinned.get("ecu_family"),
                "ecu_variant": pinned.get("ecu_variant"),
            }
            refreshed += 1
            if args.write:
                path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
            print(f"  ↻ {path.name}: family={pinned.get('ecu_family')!r} "
                  f"variant={pinned.get('ecu_variant')!r}", flush=True)
        print(f"re-pinned {refreshed} family recipes", flush=True)
    for family in sorted(declared):
        if only is not None and family not in only:
            continue
        if family in covered:
            continue
        is_unobserved = bool(observed) and family not in observed
        if is_unobserved and not args.include_unobserved:
            unreachable.append(family)
            continue

        candidates = []
        for module in declared[family]:
            base = bases.get(module)
            if base:
                candidates.append(base)
        if not candidates:
            failed.append((family, "no base recipe for its extractor"))
            continue

        hit = None
        tried = 0
        for module in declared[family]:
            base = bases.get(module)
            if not base:
                continue
            attempts = variants(base, family, base["expect"]["ecu_family"])
            attempts += marker_variants(base, family, module)
            attempts += header_variants(base, module)
            attempts += minimal_variants(base, family, module)
            for candidate in attempts:
                tried += 1
                try:
                    data = build_file(candidate)
                except ValueError:
                    continue
                if claimed_by(data) not in declared[family]:
                    # Right key, wrong extractor — a fixture that only looks
                    # like coverage. The wizard asks "does my signature also
                    # fire on another family's file", and a file claimed by a
                    # *different* extractor answers a different question.
                    continue
                result = identify_ecu(data, "mimic.mimic")
                if family in (result.get("ecu_family"), result.get("ecu_variant")):
                    hit = (candidate, result)
                    break
            if hit:
                break

        if not hit:
            # Record what the base recipe *does* report so a failure can be
            # hand-authored later without re-deriving the starting point.
            probe = {}
            for module in declared[family]:
                base = bases.get(module)
                if not base:
                    continue
                try:
                    data = build_file(base)
                except ValueError:
                    continue
                got = identify_ecu(data, "mimic.mimic")
                probe[module] = {
                    "base_recipe": BASE_FILES.get(module),
                    "family": got.get("ecu_family"),
                    "variant": got.get("ecu_variant"),
                }
            failed.append((family, "no variant produced it"))
            failed_probe[family] = probe
            print(f"  ✗ {family} ({tried} candidates tried)", flush=True)
            continue
        candidate, result = hit
        candidate["ecu_family"] = result.get("ecu_family")
        candidate["expect"] = {
            "manufacturer": result.get("manufacturer"),
            "ecu_family": result.get("ecu_family"),
            # Pinned explicitly: for the extractors that keep the whole family
            # distinction in the variant (EDC16 reports "EDC16" for ten
            # different C-numbers) the family alone would gate nothing.
            "ecu_variant": result.get("ecu_variant"),
        }
        candidate["notes"] = (
            f"Family-level fixture derived from the extractor's base recipe by steering "
            f"the ident payload to {family!r}; identify reports "
            f"family={result.get('ecu_family')!r} variant={result.get('ecu_variant')!r}."
        )
        candidate["derived_from"] = dict(candidate.get("derived_from", {}),
                                         family_target=family)
        print(f"  ✓ {family} → {result.get('ecu_family')!r} "
              f"({tried} candidates tried)", flush=True)
        # Keep the dots: ``ME71`` and ``ME7.1`` are different family keys and
        # must not collide on the same file name (they did, and the base recipe
        # for the ME7 extractor was silently overwritten).
        name = (f"{candidate['manufacturer'].lower()}-"
                f"{family.lower().replace(' ', '-').replace('+', 'p')}.json")
        written.append((family, name))
        if is_unobserved:
            unobserved_hits.append(family)
        if args.write:
            path = RECIPE_DIR / name
            # Never clobber a hand-authored base fixture that happens to share
            # the name (``bosch-me7910.json`` is both the ME7910 key and the
            # ME7.9.10 extractor's only working fixture).
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if "family_target" not in existing.get("derived_from", {}):
                    print(f"      ! {name} left alone (hand-authored base recipe)")
                    continue
            path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

    print(f"family keys declared: {len(declared)} | already covered: {len(covered)}")
    print(f"  ✓ steered: {len(written)}")
    for family, name in written:
        flag = " [never seen in a real file]" if family in unobserved_hits else ""
        print(f"      {family:14} → {name}{flag}")
    print(f"  ✗ failed: {len(failed)}")
    for family, why in failed:
        print(f"      {family:14} {why}")
    print(f"  · skipped as never seen in a real file: {len(unreachable)}")
    print("      " + ", ".join(unreachable[:20]) + ("…" if len(unreachable) > 20 else ""))

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "declared": sorted(declared),
                    "covered_before": sorted(covered),
                    "steered": [{"family": f, "recipe": n} for f, n in written],
                    "steered_unobserved": sorted(unobserved_hits),
                    "failed": [
                        {"family": f, "why": w, "probe": failed_probe.get(f, {})}
                        for f, w in failed
                    ],
                    "skipped_unobserved": sorted(unreachable),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
