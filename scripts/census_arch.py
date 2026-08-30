"""Arch census — identify the CPU of every unserved family in the corpus.

Dev-only tool (never a CI/runtime dependency).  For each ECU family in
``tests/data/ECUs`` that :func:`arch_for_family` does **not** already map
to a decoder, run the same CPU-detection cascade the engine uses
(:func:`detect_arch`) on a representative binary and report the winning
architecture, backed by the cascade's own gate: decoded instructions must
plausibly reference the binary's map-table data spans (random garbage
almost never does).

Families the cascade cannot classify are the interesting ones — they are
the "needs a new decoder" cases (8051 has no capstone support, exactly
like C166 did; 68HC11/M68K/PPC need their refs extractors written).

Corpus-gated: exits 0 with a note when ``tests/data/`` is absent (the
corpus is gitignored and CI never has it).

Usage::

    uv run python scripts/census_arch.py            # sweep all families
    uv run python scripts/census_arch.py <bin> ...  # spot-check specific files
    uv run python scripts/census_arch.py --json notes/arch/census.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from openremap.core.arch import arch_for_family
from openremap.core.arch.detect import detect_arch
from openremap.core.arch.refs import collect_xrefs
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.layout import code_regions_from_layout, segment
from openremap.core.services.maps.map_hunter import scan_map_tables
from openremap.core.services.maps.xrefs import _table_spans

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "data" / "ECUs"


def _median_file(paths: list[Path]) -> Path:
    """Deterministic representative: the file closest to the median size."""
    ordered = sorted(paths, key=lambda p: p.stat().st_size)
    return ordered[len(ordered) // 2]


def _family_groups() -> dict[str, list[Path]]:
    """Group corpus files by their family directory path."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(CORPUS.rglob("*")):
        if path.is_file():
            groups[str(path.parent)].append(path)
    return dict(groups)


def _census_row(path: Path) -> dict:
    data = path.read_bytes()
    ident = identify_ecu(data, path.name)
    manuf = ident.get("manufacturer")
    family = ident.get("ecu_family")
    mapping = arch_for_family(manuf, family)

    row: dict = {
        "dir": str(path.parent.relative_to(CORPUS)),
        "file": path.name,
        "size": len(data),
        "manufacturer": manuf,
        "family": family,
        "served": mapping is not None,
        "arch": mapping[0] if mapping else None,
    }
    if mapping is not None:
        return row  # already served — no classification needed

    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)
    spans = _table_spans(tables)
    row["tables"] = len(tables)
    row["code_regions"] = len(codes)

    if not codes or not spans:
        row["result"] = "no_gate"  # nothing to gate on → cannot classify
        return row

    xr = detect_arch(data, codes, ident.get("ecu_endian"), spans)
    row["result"] = xr.status
    row["detected_arch"] = xr.arch
    row["insns"] = xr.insn_count
    row["refs"] = len(xr.referenced)
    row["skip_reason"] = xr.skip_reason
    return row


def _fmt(row: dict) -> str:
    fam = (row.get("family") or "?") or "?"
    if row["served"]:
        return f"{fam:<14} served={row['arch']:<8} {row['file'][:30]}"
    res = row.get("result")
    if res == "no_gate":
        return (f"{fam:<14} {row['file'][:26]:<28} no-gate "
                f"(tables={row['tables']} code={row['code_regions']})")
    if res == "ok":
        return (f"{fam:<14} {row['file'][:26]:<28} -> {row['detected_arch']}  "
                f"insns={row['insns']} refs={row['refs']}")
    return (f"{fam:<14} {row['file'][:26]:<28} -> {res} "
            f"({row.get('skip_reason')})")


def _sweep(json_path: str | None) -> None:
    if not CORPUS.is_dir():
        print(f"corpus absent at {CORPUS} — nothing to census (exit 0)")
        return

    seen: set[tuple] = set()
    rows: list[dict] = []
    for dir_key, paths in _family_groups().items():
        rep = _median_file(paths)
        row = _census_row(rep)
        key = (row["manufacturer"], row["family"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    rows.sort(key=lambda r: (str(r["family"] or ""), r["dir"]))
    for r in rows:
        print(_fmt(r), flush=True)

    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(rows, indent=2))
        print(f"\nwrote {json_path}")


def _spot(paths: list[str]) -> None:
    for p in paths:
        row = _census_row(Path(p))
        print("=" * 78)
        print(f"{row['dir']}/{row['file']}  size={row['size']}")
        print(f"identify -> {row['manufacturer']} / {row['family']}  "
              f"served={row['served']} arch={row['arch']}")
        if row["served"]:
            continue
        print(f"tables={row.get('tables')} code_regions={row.get('code_regions')}")
        print(f"result={row.get('result')} detected={row.get('detected_arch')} "
              f"insns={row.get('insns')} refs={row.get('refs')} "
              f"skip={row.get('skip_reason')}")


def main() -> None:
    args = sys.argv[1:]
    if "--json" in args:
        i = args.index("--json")
        json_path = args[i + 1]
        rest = [a for j, a in enumerate(args) if j not in (i, i + 1)]
    else:
        json_path = None
        rest = args

    if rest:
        _spot(rest)
    else:
        _sweep(json_path)


if __name__ == "__main__":
    main()
