"""
Generate synthetic tuned variants of real ECU binaries — map-aware edits.

For every family directory under tests/data/ECUs, pick ONE stock binary
(the first file, sorted) and generate N tuned variants.  Edits are
structurally honest "tunes":

- the stock is scanned once (scan_map_tables),
- candidate tables are filtered to those inside ``calibration`` layout
  regions (segment() with the same scan — no double scan),
- per variant (seed-derived): up to K high-score maps are picked and a
  fraction of their data cells are changed by +5..20% (clamped), axes
  and everything else untouched.

Ident/metadata is NOT touched (same match key as the stock), file size is
identical, and — honest limitation — checksums are left stale (a real
tuner's tool would recompute them; this is exactly what makes these files
the perfect fixtures for the future checksum work).

Deterministic: same seed + same corpus → same tunes.  A manifest with
ground truth (which maps, how many cells, hashes) is written next to the
output files.

Usage:
    uv run python benchmarks/make_synthetic_tunes.py
    uv run python benchmarks/make_synthetic_tunes.py --variants 5
    uv run python benchmarks/make_synthetic_tunes.py --dry-run
"""

from __future__ import annotations

import hashlib
import json
import random
import struct
import sys
import time
from pathlib import Path

from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import MapTable, scan_map_tables

ROOT = Path(__file__).parent.parent
ECU_DIR = ROOT / "tests" / "data" / "ECUs"
OUT_DIR = ROOT / "tests" / "data" / "synthetic-tunes"

DEFAULT_VARIANTS = 5
SEED_BASE = 20260813

# Tune profile
_MAPS_PER_VARIANT = 6       # up to this many maps edited per variant
_CELL_FRACTION = 0.4        # fraction of cells changed per map
_DELTA_MIN, _DELTA_MAX = 0.05, 0.20  # +5% .. +20% per cell
_CANDIDATE_POOL = 12        # top-N maps by score the variant picks from


def _read_cells(data: bytes, t: MapTable) -> list[int]:
    """Read cell values honoring stride (compound halves)."""
    row_bytes = t.cols * t.cell_width
    stride = t.stride if t.stride is not None else row_bytes
    le = t.byte_order == "little"
    cells: list[int] = []
    for r in range(t.rows):
        row_off = t.offset + r * stride
        if t.cell_width == 1:
            cells.extend(data[row_off : row_off + t.cols])
        else:
            fmt = f"{'<' if le else '>'}{t.cols}H"
            cells.extend(struct.unpack_from(fmt, data, row_off))
    return cells


def _write_cells(buf: bytearray, t: MapTable, cells: list[int]) -> None:
    """Write cell values back honoring stride."""
    row_bytes = t.cols * t.cell_width
    stride = t.stride if t.stride is not None else row_bytes
    le = t.byte_order == "little"
    idx = 0
    for r in range(t.rows):
        row_off = t.offset + r * stride
        if t.cell_width == 1:
            for c in range(t.cols):
                buf[row_off + c] = cells[idx] & 0xFF
                idx += 1
        else:
            for c in range(t.cols):
                struct.pack_into(
                    ("<H" if le else ">H"), buf, row_off + c * 2, cells[idx] & 0xFFFF
                )
                idx += 1


def _edit_map(
    buf: bytearray, stock: bytes, t: MapTable, rng: random.Random
) -> int:
    """Modify a fraction of *t*'s cells in place (+5..20%, clamped).
    Returns the number of cells changed."""
    cells = _read_cells(stock, t)
    max_val = 0xFF if t.cell_width == 1 else 0xFFFF
    changed = 0
    for i, v in enumerate(cells):
        if rng.random() >= _CELL_FRACTION:
            continue
        pct = rng.uniform(_DELTA_MIN, _DELTA_MAX)
        delta = max(1, int(round(v * pct)))
        new = min(max_val, max(0, v + delta))
        if new == v:
            new = min(max_val, v + 1)
        cells[i] = new
        changed += 1
    if changed:
        _write_cells(buf, t, cells)
    return changed


def _pick_stock(family_dir: Path) -> Path | None:
    files = sorted(p for p in family_dir.rglob("*") if p.is_file())
    return files[0] if files else None


def _family_dirs() -> list[Path]:
    dirs: list[Path] = []
    for brand in sorted(p for p in ECU_DIR.iterdir() if p.is_dir()):
        for fam in sorted(p for p in brand.iterdir() if p.is_dir()):
            dirs.append(fam)
    return dirs


def generate(variants: int = DEFAULT_VARIANTS, dry_run: bool = False) -> dict:
    manifest = {
        "generator": "benchmarks/make_synthetic_tunes.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seed_base": SEED_BASE,
        "variants_per_family": variants,
        "tunes": [],
        "skipped": [],
    }

    family_dirs = _family_dirs()
    for fi, fam in enumerate(family_dirs, 1):
        rel = fam.relative_to(ECU_DIR)
        stock_path = _pick_stock(fam)
        if stock_path is None:
            manifest["skipped"].append({"family": str(rel), "reason": "no files"})
            continue

        stock = stock_path.read_bytes()

        # One scan per family — reused for all variants AND the segmenter.
        t0 = time.perf_counter()
        tables = scan_map_tables(stock, min_score=0.55, max_series_tables=16)
        regions = segment(stock, tables=tables)
        cal_tables = [
            t
            for t in tables
            if t.score >= 0.85
            and any(
                r.kind == "calibration" and r.start <= t.offset < r.end
                for r in regions
            )
        ]
        scan_s = time.perf_counter() - t0

        if not cal_tables:
            manifest["skipped"].append(
                {"family": str(rel), "reason": "no calibration tables found"}
            )
            print(
                f"[{fi}/{len(family_dirs)}] SKIP {rel} (no calibration tables, "
                f"scan {scan_s:.1f}s)",
                file=sys.stderr,
            )
            continue

        top = sorted(cal_tables, key=lambda t: t.score, reverse=True)[
            : _CANDIDATE_POOL
        ]

        for v in range(1, variants + 1):
            rng = random.Random(SEED_BASE * 100 + v)
            chosen = rng.sample(top, min(_MAPS_PER_VARIANT, len(top)))
            buf = bytearray(stock)
            maps = []
            cells_total = 0
            for t in chosen:
                changed = _edit_map(buf, stock, t, rng)
                cells_total += changed
                maps.append(
                    {
                        "offset": t.offset,
                        "cols": t.cols,
                        "rows": t.rows,
                        "cell_width": t.cell_width,
                        "byte_order": t.byte_order,
                        "stride": t.stride,
                        "cells_changed": changed,
                    }
                )

            out_name = stock_path.name + f".tuned{v}.bin"
            out_path = OUT_DIR / rel / out_name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(bytes(buf))

            manifest["tunes"].append(
                {
                    "family": str(rel),
                    "variant": v,
                    "stock": str(stock_path.relative_to(ROOT)),
                    "tuned": str(out_path.relative_to(ROOT)),
                    "size": len(buf),
                    "tuned_sha256": hashlib.sha256(buf).hexdigest(),
                    "maps_touched": len([m for m in maps if m["cells_changed"]]),
                    "cells_changed": cells_total,
                    "maps": maps,
                }
            )

        print(
            f"[{fi}/{len(family_dirs)}] {rel}: {variants} variant(s), "
            f"{len(top)} candidate map(s), scan {scan_s:.1f}s",
            file=sys.stderr,
        )

    return manifest


def main() -> None:
    args = sys.argv[1:]
    variants = DEFAULT_VARIANTS
    if "--variants" in args:
        variants = int(args[args.index("--variants") + 1])
    dry_run = "--dry-run" in args

    manifest = generate(variants=variants, dry_run=dry_run)

    if not dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "manifest.json").write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        print(
            f"Done: {len(manifest['tunes'])} tunes, "
            f"{len(manifest['skipped'])} families skipped → {OUT_DIR / 'manifest.json'}",
            file=sys.stderr,
        )
    else:
        print(f"dry run: would generate {len(manifest['tunes'])} tunes")


if __name__ == "__main__":
    main()
