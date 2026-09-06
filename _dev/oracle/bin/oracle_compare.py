#!/usr/bin/env python3
"""Generic oracle comparison: identify cache vs oracle label TSV.

Reads ``cache/identify.jsonl`` (one JSON object per corpus file) and an oracle
label TSV (header row, first column ``path`` as the join key), then compares
configured field pairs per row. Outputs a per-row report TSV plus an aggregate
summary, and optionally a 2-way cross-tabulation.

Field pairs are given as ``ORACLE_COL:IDENTIFY_FIELD`` (repeat ``--cmp``).
Normalization uppercases and strips whitespace/underscores/dashes so cosmetic
differences don't count as mismatches; anything beyond that is reported as a
mismatch (the point of an oracle is to surface real naming gaps).

Usage:
    .venv/bin/python oracle/bin/oracle_compare.py \
        --cache oracle/cache/identify.jsonl \
        --labels oracle/labels/subarudefs.tsv \
        --out oracle/reports/subarudefs.tsv \
        --cmp romid:software_version --cmp memmodel:ecu_family \
        --cross ecu_family,memmodel

Local scratch only (oracle/ is gitignored).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def normalize(value) -> str:
    if value is None:
        return ""
    return re.sub(r"[_\-\s]+", "", str(value)).upper()


def load_cache(path: Path) -> dict:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        out[obj.get("path", "")] = obj
    return out


def load_labels(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return rows
    header = lines[0].rstrip("\n").split("\t")
    for line in lines[1:]:
        if not line.strip():
            continue
        vals = line.rstrip("\n").split("\t")
        rows.append(dict(zip(header, vals)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="oracle/cache/identify.jsonl")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cmp", action="append", default=[],
                    help="ORACLE_COL:IDENTIFY_FIELD (repeatable)")
    ap.add_argument("--cross", default=None,
                    help="IDENTIFY_FIELD,ORACLE_COL to cross-tabulate")
    args = ap.parse_args()

    cmps = []
    for spec in args.cmp:
        if ":" not in spec:
            print(f"bad --cmp '{spec}' (want ORACLE_COL:IDENTIFY_FIELD)",
                  file=sys.stderr)
            return 2
        ocol, ifield = spec.split(":", 1)
        cmps.append((ocol, ifield))

    cache = load_cache(Path(args.cache))
    labels = load_labels(Path(args.labels))
    print(f"cache: {len(cache)} files, labels: {len(labels)} rows", file=sys.stderr)

    # Per-comparison aggregate stats.
    stats = {ocol: {"agree": 0, "mismatch": 0, "oracle_only": 0,
                    "identify_null": 0, "no_join": 0} for ocol, _ in cmps}
    rows_out = []

    for row in labels:
        path = row.get("path", "")
        ident = cache.get(path)
        outrow = {"path": path}
        if ident is None:
            for ocol, _ in cmps:
                stats[ocol]["no_join"] += 1
                outrow[ocol + "__verdict"] = "no-cache-entry"
            rows_out.append(outrow)
            continue

        for ocol, ifield in cmps:
            oval = row.get(ocol, "")
            ival = ident.get(ifield)
            onorm = normalize(oval)
            inorm = normalize(ival)
            if onorm == "":
                verdict = "oracle_only"  # oracle has no label for this field
                stats[ocol]["oracle_only"] += 1
            elif inorm == "":
                verdict = "identify_null"  # oracle labels it, we don't
                stats[ocol]["identify_null"] += 1
            elif onorm == inorm:
                verdict = "agree"
                stats[ocol]["agree"] += 1
            else:
                verdict = "mismatch"
                stats[ocol]["mismatch"] += 1
            outrow[ocol + "__oracle"] = str(oval)
            outrow[ocol + "__identify"] = "" if ival is None else str(ival)
            outrow[ocol + "__verdict"] = verdict
        rows_out.append(outrow)

    # Write per-row report.
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = ["path"]
        for ocol, ifield in cmps:
            cols += [ocol + "__oracle", ocol + "__identify", ocol + "__verdict"]
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\t".join(cols) + "\n")
            for r in rows_out:
                fh.write("\t".join(r.get(c, "") for c in cols) + "\n")
        print(f"report -> {out}")

    # Aggregate summary.
    print("\n=== aggregate (oracle_col -> verdict) ===")
    for ocol, _ in cmps:
        s = stats[ocol]
        total = sum(s.values())
        print(f"{ocol}: agree={s['agree']} mismatch={s['mismatch']} "
              f"identify_null={s['identify_null']} oracle_only={s['oracle_only']} "
              f"no_join={s['no_join']}  (n={total})")

    # Optional cross-tabulation.
    if args.cross and "," in args.cross:
        ifield, ocol = args.cross.split(",", 1)
        table: dict[tuple[str, str], int] = {}
        for row in labels:
            ident = cache.get(row.get("path", ""))
            if ident is None:
                continue
            key = (normalize(ident.get(ifield)), normalize(row.get(ocol, "")))
            table[key] = table.get(key, 0) + 1
        print(f"\n=== cross-tab {ifield} (rows) x {ocol} (cols) ===")
        ivals = sorted({k[0] for k in table})
        ovals = sorted({k[1] for k in table})
        header = "identify\\oracle\t" + "\t".join(o or "(none)" for o in ovals)
        print(header)
        for iv in ivals:
            cells = "\t".join(str(table.get((iv, ov), 0)) for ov in ovals)
            print(f"{iv or '(none)'}\t{cells}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
