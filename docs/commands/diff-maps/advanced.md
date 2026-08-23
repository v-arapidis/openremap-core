---
title: diff-maps — advanced
description: Compare two binaries at map level — every flag, grouping, JSON output.
---

# `openremap diff-maps`

Calibration-level diff. Scans a stock and a tuned binary for calibration
tables, matches them by axis fingerprint (the X/Y breakpoint value tuples),
and reports cell-by-cell changes for each matched pair.

This is the map-level counterpart to `cook` (byte-level diff): instead of raw
byte changes you get *which maps changed and by how much* — useful for
auditing a tune without a manufacturer database.

---

## Usage

```bash
openremap diff-maps <STOCK> <TUNED> [OPTIONS]
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--min-score`, `-s` | `0.55` | Minimum table score in `[0, 1]`. Lower than `scan-maps`' default to avoid missing changed maps. |
| `--threshold`, `-t` | `0.0` | Only show maps with max absolute cell change ≥ threshold. |
| `--top`, `-n` | `50` | Max matched maps to show. |
| `--compact` | off | Group output — show only the top-3 changed maps per axis group. |
| `--verbose`, `-v` | off | Show before/after cell grids for each changed map. |
| `--json` | off | Output as JSON instead of human-readable text. |
| `--export <dir>` | off | Write a Markdown report (`diff.md`) with before/after grids, changed cells highlighted. |
| `--region`, `-r` | (whole file) | Restrict scanning to a byte range: `0xSTART-0xEND`. |
| `--max-series-tables` | `16` | Max consecutive shared-axis tables to probe (1 = off). |
| `--recipe <path>` | off | Cross-reference a `.remap` recipe: mark which changed cells each instruction covers and report changed cells NOT in the recipe (untracked changes). |
| `--annotate <path>` | off | With `--recipe`: write the recipe augmented with a schema 4.4 `maps` layer to this path. |

---

## How matching works

1. Both files are scanned with the same structural scanner as `scan-maps`.
2. Tables are matched by **axis fingerprint** — identical X and Y axis value
   tuples — with offset proximity disambiguation when two tables share axes.
3. Matched pairs are diffed cell-by-cell: `tuned − stock`, plus per-cell
   percentage change.

Maps whose axis breakpoints changed between stock and tuned appear as
**unmatched** rather than matched — this is intentional. Tuners rarely change
breakpoints; when they do, the map structure itself has changed.

The report also lists:
- **Unmatched maps** — only-in-stock / only-in-tuned tables.
- **Changed-block promotion** — changed bytes in tables the axis scanner
  missed (e.g. flat-Y layouts).

### Recipe cross-reference (`--recipe`)

Pass a `.remap` recipe to connect the byte-level and map-level views.  Every
changed cell is checked against the recipe instructions (byte-range overlap
on the stock side):

```
◆ Recipe cross-reference (tune.remap)
  79 instruction(s) touch 29 map(s) — 738 of the changed cells are covered.
  Untracked: 0 changed cell(s) not present in the recipe.
```

Changed cells **not** covered by any recipe instruction are *untracked* —
bytes that changed in the tuned file but the recipe does not explain (extra
edits, flags, checksums).  Add `--annotate out.remap` to write the recipe
back with a schema 4.4 `maps` layer (map descriptors + instruction refs).

---

## Example output

```
  stock.bin vs tuned.bin

  1,985 tables found in stock · 1,990 in tuned · 342 matched pairs

  Map 0x000376F2   32×16   max +23   avg +1.9   +18.3%   Group A
  Map 0x002214D4   32×6    max  +8   avg +0.4   +12.1%   Group A
  …
```

Grouping is by axis fingerprint — maps that share RPM×Load breakpoint axes
(fuel, timing, boost families) appear together instead of scattered by delta.

---

## JSON output

```bash
openremap diff-maps stock.bin tuned.bin --json
```

```json
{
  "stock": "stock.bin",
  "tuned": "tuned.bin",
  "matched": 342,
  "unmatched_stock_only": 12,
  "unmatched_tuned_only": 9,
  "groups": [
    {"group": "A", "maps": 2, "cols": 32, "rows": 16, "x_axis": [0, 500, 800, "…"], "y_axis": ["…"]}
  ],
  "matches": [
    {
      "offset": 227058,
      "cols": 32,
      "rows": 16,
      "group": "A",
      "max_change": 23,
      "avg_change": 1.9,
      "pct_changed": 18.3
    }
  ]
}
```

---

## Notes

- The scanner is **structural, not semantic** — it does not know whether a
  map is fuel, timing, or boost. `--classify` on `scan-maps` gives
  probabilistic labels.
- Large scans take a few seconds per file; results are deterministic.
- Changes in flags, checksums, or VIN areas do not appear here — use `cook`
  for the complete byte-level picture.

---

## Related commands

| Command | Reference |
|---|---|
| `openremap scan-maps` | [→ scan-maps.md](../scan-maps/index.md) — structural table discovery |
| `openremap cook` | [→ cook.md](../cook/index.md) — byte-level diff and recipe |

---

