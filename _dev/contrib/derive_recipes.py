#!/usr/bin/env python3
"""
Derive mimic recipes for every extractor we ship (dev tool, committed).

Emits `recipes/<manufacturer>-<family>.json` for as many extractors as it can
crack, by treating **the extractor itself as the specification**:

1. **Introspect the extractor's module** for candidate payloads and anchors —
   module constants (bytes, byte-tuples, int tuples, ASCII strings), the names
   of positional constants (`*_OFFSET*`, `*_POS*`), size constants
   (`*SIZE*`), fill/ratio constants, plus every byte-string literal in the
   module source. A payload that appears in exactly **one** extractor module is
   ranked first: it is the most likely discriminator.
2. **Search** size × fill × (payload, anchor) and then ordered payload pairs
   (which covers "`0281…` must sit next to `1037…`"), gated on the *intended*
   extractor accepting **and** `identify` reporting that extractor's
   manufacturer with a family from its own `supported_families` — so no earlier
   extractor may steal the file.
3. **Write the recipe** with the winning parameters, plus provenance.

Real values from the committed identify snapshot are used as *additional*
payload candidates and for realistic sizes — but never as the only source: the
snapshot holds the extractor's **output**, not the bytes its `can_handle`
checks. That distinction is why a snapshot-first search capped out at 5 of 12
families while the structural ones (a 4-byte Denso descriptor, a TSW string,
advancement requirements) were unreachable.

Usage:
    .venv/bin/python _dev/contrib/derive_recipes.py            # report only
    .venv/bin/python _dev/contrib/derive_recipes.py --write    # write recipes
    .venv/bin/python _dev/contrib/derive_recipes.py --only EDC15 --write
"""

from __future__ import annotations

import argparse
import collections
import importlib
import inspect
import itertools
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openremap.core.manufacturers import get_extractors  # noqa: E402
from openremap.core.services.identify.identifier import identify_ecu  # noqa: E402

RECIPE_DIR = Path(__file__).resolve().parent / "recipes"
SNAPSHOT = ROOT / "_dev/oracle/smallfixes-snapshot/after/identify.jsonl"

DEFAULT_SIZES = (32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304)
FILLS = (0xFF, 0xC3, 0x00)
SEPARATORS = (b"\xff", b".", b" ", b"\x00")
HEURISTIC_ANCHORS = (0x0, 0x1000, 0x2000, 0x4000, 0x7FD9, 0x8000, 0x10000)
MAGIC_NAMES = re.compile(r"OFFSET|_POS|_AT_|BASE|MARKER|ANCHOR", re.I)
SIZE_NAMES = re.compile(r"SIZE", re.I)
BYTE_LITERAL = re.compile(r'b"((?:[^"\\]|\\.){1,64})"')
#: Regex-ish literals that describe a *shape* rather than a fixed string, e.g.
#: ``0281\d{6}`` or ``[0-9]{4}``. The extractor wants an instance of the shape,
#: not the pattern text, so we synthesise plausible instances.
REGEX_LITERAL = re.compile(r'r?b?"([^"\n]{4,48})"')
_SHAPE = re.compile(r"\\d|\[0-9\]|\[A-Z\]|\[a-z\]|\\.\{|\.\{")


def regex_instances(pattern: str, limit: int = 8) -> list[bytes]:
    """Turn a regex-ish literal into plausible matching instances."""
    if not _SHAPE.search(pattern):
        return []
    out: list[bytes] = []
    digits = ("0281001214", "1037372735", "2537355342", "0261208376")
    letters = ("A", "B", "C", "D")
    for i in range(limit):
        text = pattern
        text = re.sub(r"\\d\{(\d+)(?:,(\d+))?\}", lambda m: "1" * int(m.group(1)), text)
        text = re.sub(r"\[\s*0-9\s*\]\s*\{(\d+)(?:,(\d+))?\}",
                      lambda m: "7" * int(m.group(1)), text)
        text = re.sub(r"\[\s*A-Z\s*\]\s*\{(\d+)(?:,(\d+))?\}",
                      lambda m: "A" * int(m.group(1)), text)
        text = re.sub(r"\\d\{(\d+),(\d+)\}", lambda m: "3" * int(m.group(1)), text)
        text = text.replace(r"\d", "5").replace("[0-9]", "8")
        text = text.replace("[A-Z]", letters[i % len(letters)])
        text = text.replace("[a-z]", "x")
        text = re.sub(r"\\x[0-9a-fA-F]{2}", "", text)
        text = text.replace(r"\s", " ").replace(r"\.", ".")
        text = re.sub(r"[\^$()|*+?{}\[\]]", "", text)
        text = text.replace("\\\\", "\\")
        if text and text.isascii() and text.isprintable():
            out.append(text.encode())
            # A digit-run variant helps patterns anchored on real part numbers.
            if any(ch.isdigit() for ch in text):
                out.append(digits[i % len(digits)].encode())
    return out


# ---------------------------------------------------------------------------
# Candidate harvesting
# ---------------------------------------------------------------------------


def _as_payload(value) -> bytes | None:
    """Turn a module constant into a candidate payload, or None."""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, (tuple, list, frozenset, set)):
        items = list(value)
        if not items:
            return None
        if all(isinstance(i, int) and 0 <= i <= 255 for i in items):
            payload = bytes(items)
        elif all(isinstance(i, bytes) for i in items) and len(items) == 1:
            payload = items[0]
        else:
            return None
    elif isinstance(value, str) and value.isascii() and 2 <= len(value) <= 64:
        payload = value.encode()
    else:
        return None
    if not (2 <= len(payload) <= 64) or b"\x00" * 3 == payload:
        return None
    return payload


def harvest(extractor) -> tuple[list[bytes], list[int], list[int], list[int]]:
    """Return (payloads, anchors, sizes, fills) candidate pools for *extractor*."""
    module = importlib.import_module(type(extractor).__module__)
    payloads: list[bytes] = []
    anchors: list[int] = []
    sizes: list[int] = []
    fills: list[int] = []

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        payload = _as_payload(value)
        if payload is not None:
            payloads.append(payload)
        if isinstance(value, slice):
            # ``FOOTER_REGION = slice(0xFFC0, 0x10000)`` — plant at the start
            # of the window the extractor searches.
            if isinstance(value.start, int) and 0 <= value.start <= 0x800000:
                anchors.append(value.start)
        if isinstance(value, int) and not isinstance(value, bool):
            if MAGIC_NAMES.search(name) and 0 < value < 0x100000:
                anchors.append(value)
            if SIZE_NAMES.search(name) and 1024 <= value <= 0x800000:
                sizes.append(value)
            if re.search(r"FILL|_BYTE\b", name) and 0 <= value <= 255:
                fills.append(value)

    # Assemble markers from byte-class constants: many extractors check a
    # descriptor *byte by byte* against separate constants
    # (``marker[0] in MARKER_BYTE0`` …), so the literal never appears as one
    # payload. The cartesian product of the candidate *elements* recovers it:
    # (0x33,0x34) × (0x41,0x61,0xA1,0xA2) × (b"\x00\x01",…) → b"\x33\x41\x00\x01".
    classes: list[list[bytes]] = []
    for name, value in vars(module).items():
        if name.startswith("_") or not isinstance(value, (tuple, list)):
            continue
        items = list(value)
        if items and all(isinstance(i, int) and 0 <= i <= 255 for i in items):
            classes.append([bytes([i]) for i in items[:3]])
        elif items and all(isinstance(i, bytes) and 1 <= len(i) <= 2 for i in items):
            classes.append(list(items[:3]))
    if 2 <= len(classes) <= 4:
        for combo in itertools.product(*classes):
            joined = b"".join(combo)
            if 2 <= len(joined) <= 8:
                payloads.append(joined)

    source = inspect.getsource(module)
    # Anchors that only exist as *slice literals in the code* — the extractor
    # checks ``data[0x1FFC:0x2000]`` or ``data[NAME + 0x2000]`` without ever
    # naming an OFFSET constant. Missing these was why the Denso families
    # looked unreachable: their marker sits at a bare hex literal.
    slice_expr = re.compile(r"data\[\s*([^\]:]+?)\s*[:+\]]")
    for expr in slice_expr.findall(source):
        for token in re.findall(r"0x[0-9A-Fa-f]+|[A-Z][A-Z0-9_]{2,}", expr):
            value = None
            if token.startswith("0x"):
                value = int(token, 16)
            else:
                candidate = vars(module).get(token)
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    value = candidate
                elif isinstance(candidate, slice) and isinstance(candidate.start, int):
                    value = candidate.start
            if value is not None and 0 < value < 0x800000:
                anchors.insert(0, value)  # code-derived anchors win
    for literal in BYTE_LITERAL.findall(source):
        try:
            decoded, _ = codecs.escape_decode(literal.encode("utf-8"))
        except Exception:
            continue
        if 2 <= len(decoded) <= 64:
            payloads.append(decoded)

    # Regex-ish literals describe a *shape*: the extractor wants an instance of
    # it, so synthesise plausible matching strings (part numbers, SW codes …).
    # Sibling patterns.py modules hold most of them.
    sources = [source]
    patterns_file = Path(module.__file__).with_name("patterns.py")
    if patterns_file.is_file():
        sources.append(patterns_file.read_text(encoding="utf-8"))
    for text in sources:
        for literal in REGEX_LITERAL.findall(text):
            payloads.extend(regex_instances(literal))

    # Deterministic order: bytes payloads first (explicit markers), then longer
    # ASCII strings (more likely to be discriminators).
    def rank(p: bytes) -> tuple:
        printable = all(32 <= b < 127 for b in p)
        return (0 if not printable else 1, -len(p), p)

    payloads = sorted(set(payloads), key=rank)
    return payloads, sorted(set(anchors)), sorted(set(sizes)), sorted(set(fills))


def snapshot_rows() -> dict[str, dict]:
    """family key -> a real identify row (for realistic sizes and strings)."""
    out: dict[str, dict] = {}
    if not SNAPSHOT.is_file():
        return out
    for line in SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.setdefault(row.get("ecu_family") or "", row)
    return out


def family_uniqueness(extractors: list) -> collections.Counter:
    """How many extractor modules contain each harvested payload."""
    counter: collections.Counter = collections.Counter()
    for extractor in extractors:
        payloads, _, _, _ = harvest(extractor)
        counter.update(set(payloads))
    return counter


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def accepts(extractor, data: bytes) -> bool:
    try:
        return bool(extractor.can_handle(data))
    except Exception:
        return False


def identified_as(data: bytes, extractor) -> dict | None:
    """Return the identity if *extractor* (and no earlier one) claims *data*."""
    result = identify_ecu(data, "mimic.mimic")
    if result.get("manufacturer") != extractor.name:
        return None
    if result.get("ecu_family") not in (extractor.supported_families or []):
        return None
    return result


def try_recipe(extractor, snapshot: dict[str, dict], budget: float, limit: int):
    """Search for the parameters that make *extractor* claim a generated file.

    Phases run **cheapest-first across every (size, fill) combination** rather
    than nesting the other way round: with the loops the other way, one wrong
    size burns the entire budget before the right one is ever tried. Each phase
    yields candidate plant-lists; the first that both satisfies the extractor
    and survives the "nobody earlier claims it" gate wins, and is then
    minimised (plants that are not needed are dropped, so the committed recipe
    shows only what actually matters).
    """
    payloads, anchors, sizes, fills = harvest(extractor)
    families = extractor.supported_families or []

    unique = family_uniqueness(get_extractors())
    real: list[bytes] = []
    for fam in families:
        for s in (snapshot.get(fam, {}).get("raw_strings") or [])[:20]:
            if s.isascii() and 2 <= len(s) <= 64:
                real.append(s.encode())
    payloads = [p for p in payloads if unique[p] == 1] + payloads
    # Real strings from the snapshot go in the HEAD, not the tail: they are the
    # ones carrying genuine formats (Denso CAL IDs like ``A8DH100E``), and the
    # multi-feature phase only ever looks at the head.
    payloads = list(dict.fromkeys(list(real) + payloads))[:64]

    size_pool = [
        s for s in dict.fromkeys(
            [snapshot.get(f, {}).get("file_size") for f in families if snapshot.get(f)]
            + list(sizes)
            + list(DEFAULT_SIZES)
        ) if s
    ]
    fill_pool = list(dict.fromkeys(list(fills) + list(FILLS)))
    # Fill-major order: every size is tried with the primary fill before the
    # exotic fills are considered. Fill rarely matters (EDC3/EDC15 are the
    # exceptions); size and payload almost always do.
    combos = [(s, f) for f in fill_pool for s in size_pool]

    trials = 0
    deadline = time.monotonic() + budget
    work: dict[tuple[int, int], bytearray] = {}

    class _Budget(Exception):
        pass

    def pool_for(size: int) -> list[int]:
        # Extractor-derived anchors first: they are the ones it actually checks.
        return list(dict.fromkeys(
            list(anchors) + list(HEURISTIC_ANCHORS)
            + [size - 0x40, size - 0x100, size // 2]
        ))

    def attempt(size: int, fill: int, plants: list[tuple[int, bytes]]) -> dict | None:
        nonlocal trials
        trials += 1
        if trials > limit or time.monotonic() > deadline:
            raise _Budget()
        key = (size, fill)
        if key not in work:
            work[key] = bytearray(bytes([fill]) * size)
        buf = work[key]

        # Mutate in place and restore afterwards. Copying a multi-megabyte
        # buffer on every trial — not the size of the search space — was what
        # exhausted the budget.
        undo: list[tuple[int, bytes]] = []
        for pos, payload in plants:
            if pos < 0 or pos + len(payload) > size:
                for p, old in reversed(undo):
                    buf[p:p + len(old)] = old
                return None
            undo.append((pos, bytes(buf[pos:pos + len(payload)])))
            buf[pos:pos + len(payload)] = payload
        try:
            # A bytearray satisfies every byte operation the *Python* checks use
            # (slicing, `in`, regex) and avoids an immutable copy per trial. The
            # Rust extension insists on real bytes, so convert only on a hit.
            claimed = accepts(extractor, buf)
            found = identified_as(bytes(buf), extractor) if claimed else None
        finally:
            for pos, old in reversed(undo):
                buf[pos:pos + len(old)] = old

        if not found:
            return None
        return {
            "size": size,
            "fill": f"{fill:02X}",
            "plants": [
                {"at": hex(pos), "seq": [{"hex": payload.hex()}]}
                for pos, payload in plants
            ],
            "observed": found,
        }

    def minimise(size: int, fill: int, plants: list[tuple[int, bytes]]):
        """Drop plants that are not needed — a recipe should show what matters."""
        kept = list(plants)
        for candidate in list(plants):
            trial = [p for p in kept if p != candidate]
            if not trial:
                continue
            try:
                if attempt(size, fill, trial):
                    kept = trial
            except _Budget:
                break
        return kept

    # -- phases: each yields candidate plant-lists for one (size, fill) -------

    def phase_spray(size: int, _fill: int):
        pool = pool_for(size)
        for payload in payloads:
            usable = [p for p in pool if p + len(payload) <= size]
            if usable:
                yield [(p, payload) for p in usable]

    def phase_single(size: int, _fill: int):
        pool = pool_for(size)
        for payload in payloads:
            for pos in pool:
                if pos + len(payload) <= size:
                    yield [(pos, payload)]

    def phase_pairs(size: int, _fill: int):
        pool = pool_for(size)
        for first, second in itertools.permutations(payloads[:10], 2):
            for sep in SEPARATORS:
                joined = first + sep + second
                for pos in pool:
                    if pos + len(joined) <= size:
                        yield [(pos, joined)]

    def phase_multi(size: int, _fill: int):
        """Several DIFFERENT features at several anchors at once.

        Some extractors check a descriptor *and* something in another window
        *and* a project string elsewhere (Denso wants a 4-byte marker at
        ``base+0x1FFC``, bytes at ``base+0x2000`` and ``Copr.DENSO`` inside
        ``base+0x2010..0x2040``). Reaching those needs combinations of payloads,
        not the top-k prefix in one fixed order.
        """
        pool = pool_for(size)[:12]
        head = payloads[:10]
        for k in (2, 3):
            for anchors_k in itertools.combinations(pool, k):
                for chosen in itertools.combinations(head, k):
                    for assign in (chosen, tuple(reversed(chosen))):
                        if all(p + len(pl) <= size for p, pl in zip(anchors_k, assign)):
                            yield list(zip(anchors_k, assign))

    for phase in (phase_spray, phase_single, phase_pairs, phase_multi):
        try:
            for size, fill in combos:
                for plants in phase(size, fill):
                    hit = attempt(size, fill, plants)
                    if hit:
                        kept = minimise(size, fill, plants)
                        hit["plants"] = [
                            {"at": hex(pos), "seq": [{"hex": payload.hex()}]}
                            for pos, payload in kept
                        ]
                        hit["derived_from_phase"] = phase.__name__
                        return hit, trials, None
        except _Budget:
            return None, trials, "budget exhausted"

    return None, trials, "no combination in the search space worked"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--write", action="store_true", help="write recipes for the successes")
    ap.add_argument("--only", help="restrict to extractors whose families contain this key")
    ap.add_argument("--budget", type=float, default=20.0, help="seconds per extractor")
    ap.add_argument("--limit", type=int, default=6000, help="trials per extractor")
    args = ap.parse_args()

    extractors = get_extractors()
    if args.only:
        extractors = [
            e for e in extractors
            if args.only in (e.supported_families or [])
            or args.only.lower() in type(e).__module__.lower()
        ]
    snapshot = snapshot_rows()

    ok, failed = [], []
    for extractor in extractors:
        found, trials, reason = try_recipe(extractor, snapshot, args.budget, args.limit)
        label = f"{extractor.name}/{type(extractor).__module__.rsplit('.', 1)[0].split('.')[-1]}"
        if not found:
            failed.append((label, reason, trials))
            print(f"  ✗ {label:34} {reason} ({trials} trials)", flush=True)
            continue

        family = found["observed"]["ecu_family"]
        recipe = {
            "manufacturer": extractor.name,
            "ecu_family": family,
            "file_size": found["size"],
            "fill": found["fill"],
            "notes": (
                "Derived by _dev/contrib/derive_recipes.py from the extractor itself "
                f"({type(extractor).__module__}): payloads and anchors were harvested from "
                "its module constants and byte-string literals, then searched. Gate is "
                "manufacturer + family; extracted fields are not reproduced."
            ),
            "derived_from": {"script": "_dev/contrib/derive_recipes.py",
                             "extractor": type(extractor).__module__},
            "plants": found["plants"],
            "expect": {"manufacturer": extractor.name, "ecu_family": family},
        }
        name = f"{extractor.name.lower()}-{family.lower().replace('.', '').replace(' ', '-')}.json"
        if args.write:
            RECIPE_DIR.mkdir(parents=True, exist_ok=True)
            (RECIPE_DIR / name).write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
        ok.append(label)
        print(f"  ✓ {label:34} → {family:12} size={found['size']//1024}KB "
              f"fill={found['fill']} ({trials} trials)", flush=True)

    print(f"\n{len(ok)}/{len(extractors)} extractors cracked")
    if failed:
        print("\nfailed:")
        for label, reason, trials in failed:
            print(f"  {label:34} {reason}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
