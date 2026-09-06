#!/usr/bin/env python3
"""Bulk-identify a corpus once and cache the results.

Local scratch only (oracle/ is gitignored). Imports ``identify_ecu``
in-process so the Rust extension + extractor registry load exactly once,
instead of paying Python/import startup per file through the CLI.

The cache is oracle-agnostic: every downstream oracle comparison reads
``cache/identify.jsonl`` and never re-runs identification. Incremental via
``cache/manifest.tsv`` (path -> sha256): re-runs only files whose bytes
changed or that are new.

Usage:
    .venv/bin/python oracle/bin/identify_bulk.py \
        --corpus tests/data/ECUs --cache-dir oracle/cache [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from openremap.core.services.identify.identifier import identify_ecu

EXTS = {".bin", ".ori", ".hex", ".s19", ".srec", ".mot", ".rom"}


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in EXTS:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="tests/data/ECUs")
    ap.add_argument("--cache-dir", default="oracle/cache")
    ap.add_argument("--force", action="store_true",
                    help="re-identify every file even if sha256 is cached")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = cache_dir / "identify.jsonl"
    manifest_path = cache_dir / "manifest.tsv"

    known: dict[str, str] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                p, h = line.split("\t", 1)
                known[p] = h

    root = Path(args.corpus)
    files = list(iter_files(root))
    n_total = len(files)
    n_skip = n_done = n_err = 0

    # Identify only the files that are new or changed; append to JSONL.
    with open(jsonl_path, "a", encoding="utf-8") as out:
        for f in files:
            key = str(f)
            try:
                data = f.read_bytes()
            except OSError as exc:
                n_err += 1
                print(f"ERR read {key}: {exc}", file=sys.stderr)
                continue

            digest = hashlib.sha256(data).hexdigest()
            if not args.force and known.get(key) == digest:
                n_skip += 1
                continue

            try:
                res = identify_ecu(data=data, filename=f.name)
            except Exception as exc:  # noqa: BLE001 — never fail the whole batch
                n_err += 1
                print(f"ERR identify {key}: {exc}", file=sys.stderr)
                continue

            res["path"] = key
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
            out.flush()
            known[key] = digest
            n_done += 1

            seen = n_done + n_skip
            if seen % 200 == 0:
                print(f"... identified={n_done} cached={n_skip} errors={n_err} / {n_total}",
                      file=sys.stderr)

    with open(manifest_path, "w", encoding="utf-8") as mf:
        for k, h in sorted(known.items()):
            mf.write(f"{k}\t{h}\n")

    print(f"DONE identified={n_done} cached={n_skip} errors={n_err} total={n_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
