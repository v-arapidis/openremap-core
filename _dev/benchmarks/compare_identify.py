"""
Compare two identify captures (capture_identify.py output).

Usage:
    uv run python benchmarks/compare_identify.py baseline.json new.json

Prints:
  - summary deltas (identified count, tier shifts, avg ms)
  - per-file changes: score delta, tier up/down, identity field changes,
    new/removed confidence signals and warnings
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _by_path(snapshot: dict) -> dict:
    return {f["path"]: f for f in snapshot["files"]}


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: compare_identify.py baseline.json new.json")
        sys.exit(1)
    base = json.loads(Path(sys.argv[1]).read_text())
    new = json.loads(Path(sys.argv[2]).read_text())

    base_files = _by_path(base)
    new_files = _by_path(new)

    b_sum, n_sum = base["summary"], new["summary"]
    print("=== SUMMARY ===")
    print(
        f"identified: {b_sum['identified']} → {n_sum['identified']} "
        f"({n_sum['identified'] - b_sum['identified']:+d})"
    )
    print(
        f"avg ms:     {b_sum['avg_ms']} → {n_sum['avg_ms']} "
        f"({n_sum['avg_ms'] - b_sum['avg_ms']:+.1f})"
    )
    tiers = sorted(set(b_sum["tiers"]) | set(n_sum["tiers"]))
    for t in tiers:
        print(
            f"  tier {t:<10}: {b_sum['tiers'].get(t, 0):>5} → "
            f"{n_sum['tiers'].get(t, 0):<5} "
            f"({n_sum['tiers'].get(t, 0) - b_sum['tiers'].get(t, 0):+d})"
        )
    print()

    changed = 0
    tier_shifts = 0
    print("=== PER-FILE CHANGES ===")
    for path in sorted(set(base_files) | set(new_files)):
        b = base_files.get(path)
        n = new_files.get(path)
        if b is None:
            print(f"  + NEW FILE {path}")
            continue
        if n is None:
            print(f"  - REMOVED {path}")
            continue

        b_conf = b.get("confidence", {})
        n_conf = n.get("confidence", {})
        b_tier, n_tier = b_conf.get("tier"), n_conf.get("tier")
        b_score, n_score = b_conf.get("score", 0), n_conf.get("score", 0)

        b_sig = {tuple(s) for s in b_conf.get("signals", [])}
        n_sig = {tuple(s) for s in n_conf.get("signals", [])}
        added = sorted(n_sig - b_sig)
        removed = sorted(b_sig - n_sig)

        if (
            b_score == n_score
            and b_tier == n_tier
            and not added
            and not removed
            and b["identity"] == n["identity"]
        ):
            continue
        changed += 1
        if b_tier != n_tier:
            tier_shifts += 1
        print(f"\n  {path}")
        print(
            f"    score {b_score} → {n_score} ({n_score - b_score:+d})  "
            f"tier {b_tier} → {n_tier}"
        )
        for s in removed:
            print(f"    - signal {s}")
        for s in added:
            print(f"    + signal {s}")

    print(f"\n{changed} files changed, {tier_shifts} tier shifts")


if __name__ == "__main__":
    main()
