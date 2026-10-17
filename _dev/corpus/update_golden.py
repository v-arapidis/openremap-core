#!/usr/bin/env python3
"""
Regenerate the synthetic-corpus goldens (dev tool, committed).

Writes ``tests/corpus/cases/<id>/golden.json`` for every case in
``tests/corpus/manifest.json``, and reports what changed.

**Updating a golden is a reviewed act.**  Read ``git diff tests/corpus``,
confirm each change is intended, and commit it *together with* the engine or
generator change that caused it.  Running this merely to make a red test green
turns the corpus into decoration — the whole value is that a diff is a signal.

Usage:
    .venv/bin/python _dev/corpus/update_golden.py            # rewrite all cases
    .venv/bin/python _dev/corpus/update_golden.py --case ID  # rewrite one case
    .venv/bin/python _dev/corpus/update_golden.py --check    # write nothing, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.corpus.golden import (  # noqa: E402
    build_case,
    case_ids,
    expected_path,
    first_difference,
    get_case,
    load_expected,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--case", help="only this case id (default: all)")
    ap.add_argument(
        "--check",
        action="store_true",
        help="compare only; write nothing and exit 1 if any case drifted",
    )
    args = ap.parse_args()

    cases = [args.case] if args.case else case_ids()
    if args.case and args.case not in case_ids():
        print(f"unknown case: {args.case}", file=sys.stderr)
        return 2

    drifted = 0
    for case_id in cases:
        actual = build_case(get_case(case_id))
        path = expected_path(case_id)

        if not path.is_file():
            print(f"{case_id}: NEW ({len(json.dumps(actual)) / 1024:.1f} KB)")
            drifted += 1
            if not args.check:
                _write(path, actual)
            continue

        difference = first_difference(load_expected(case_id), actual)
        if difference is None:
            print(f"{case_id}: unchanged")
            continue

        drifted += 1
        print(f"{case_id}: CHANGED at {difference}")
        if not args.check:
            _write(path, actual)

    if args.check and drifted:
        print(
            f"\n{drifted} case(s) drifted. If intentional, run this tool without "
            " --check, read the diff, and commit it with the change.",
            file=sys.stderr,
        )
        return 1
    return 0


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"    wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
