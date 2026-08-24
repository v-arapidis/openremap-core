---
title: scan-maps — advanced
description: Structural map scan — every flag, classify, CSV export, examples.
---

# `openremap scan-maps`

Structural calibration map scanner. Finds monotonically-increasing 16-bit
sequences (RPM/load breakpoints) and the 2D data tables that follow them
without requiring manufacturer identification. Works on any binary regardless
of ECU family.

Use this to:
- **Health-check** a binary — genuine ECU files have hundreds or thousands of
  axes; encrypted or corrupt files have almost none.
- **Discover maps** in unsupported ECUs — feed the offsets into WinOLS or
  ECM Titanium as starting points.
- **Explore** a binary's structure before committing to extractor development.

Run `openremap identify <file>` first to see manufacturer and SW info; run
this after to see the map structure.

---

## Usage

```bash
openremap scan-maps <FILE> [OPTIONS]
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--top`, `-n` | `20` | Number of top-scoring tables to show. |
| `--min-score`, `-s` | `0.85` | Minimum table score in `[0, 1]`. Higher = fewer false positives. |
| `--region`, `-r` | (calibration region) | Restrict scanning to a byte range: `0xSTART-0xEND` or `START-END`. Overrides the calibration-region default. |
| `--whole-file` | off | Scan the whole file instead of only the detected calibration region — shows tables outside it. |
| `--json` | off | Output as JSON instead of human-readable text. |
| `--max-series-tables` | `16` | Max consecutive shared-axis tables to probe after each anchor. Set to `1` to disable. |
| `--show-series` | off | Group tables sharing identical X/Y axes with indented `└─` continuation rows. |

---

## Calibration-region default

By default the scan is limited to the **calibration region** — the flash
area the layout segmenter labels as calibration (sectors containing
high-score tables).  Junk tables that the scanner finds in code / erased /
mixed sectors are hidden and counted (in JSON as `tables_hidden`); the
human output notes them:

```
  304 table(s) outside the calibration region hidden — use --whole-file to scan the whole file.
```

- **`--whole-file`** scans everything (code-sector junk included).
- **`--region`** overrides the default entirely — an explicit range wins.
- **No calibration signal** (small / synthetic / unfamiliar binaries) falls
  back to whole-file behaviour automatically — the default never hides
  tables where it cannot find a calibration region.

The layout estimate is structural inference (no manufacturer database), so
it can be wrong on unusual binaries — the fallback and `--whole-file` /
`--region` overrides keep that harmless: a wrong estimate only changes
what the report shows, never recipe or patch output.

## Shared-axis detection

Real ECUs often place multiple calibration tables (fuel, timing, boost, EGR)
consecutively after a single pair of RPM×Load breakpoint axes:

```
[RPM axis][Load axis][Fuel table][Timing table][Boost table][EGR table]
```

The scanner detects the first table normally, then probes forward for
additional blocks with identical dimensions (same cols, rows, cell width)
sharing the same axes.  Each block must pass the same scoring pipeline as
the anchor — garbage, code, or mismatched-geometry data stops the series.

Use `--max-series-tables 1` to disable this and report only the primary
table per axis pair.

---

## Scoring

Each 2D table is scored on a multi-dimension heuristic:

- **Axis quality** — how smoothly monotonic each axis is (no reversals, no
  gaps, reasonable step sizes for calibration breakpoints).
- **Table smoothness** — row-to-row and column-to-column gradient continuity.
  Real calibration data is smooth; code and random data are not.
- **Dimensionality** — plausible row×column dimensions for ECU maps
  (e.g. 16×16 is common; 2×400 is not).
- **Stripe penalty** — repeated identical rows/columns (common in padding and
  lookup tables, absent in real maps).

| Score | Meaning |
|---|---|
| ≥ 0.90 | High confidence — smooth calibration surface, genuine axis values. |
| 0.85–0.90 | Plausible — likely a real calibration map; may have sharp transitions or clamp regions. |
| 0.75–0.85 | Mixed — some real maps, some encoded data. Use `--show-series` to inspect context. |
| < 0.75 | Low confidence — mostly coincidental structures. Lower `--min-score` if exploring unsupported ECUs. |

The default `--min-score` of `0.85` returns ~70% fewer tables than the old
default of `0.75` while keeping >90% of genuine calibration maps.  Lower it to
`0.55` for exhaustive scanning; raise to `0.90` for the cleanest signal.

---

## Example output

```
  original.bin

  ✓  Genuine calibration binary
  15,967 axes  •  1,985 tables  •  4,194,304 bytes

      Offset       Dim   Cells    Score      X Axis      Y Axis
  ──────────────────────────────────────────────────────────────
  0x000376F2     32×16  u16 LIT  0.977  0x00037692  0x376D2
  0x002214D4      32×6  u16 LIT  0.965  0x00221488  0x2214C8
  0x0002CBC8      16×8  u8 LIT  0.969  0x0002CB98  0x2CBB8
  0x00037BE2      32×8  u16 LIT  0.964  0x00037B92  0x37BD2
  0x00288B80     16×16  u16 LIT  0.960  0x00288B3E  0x288B5E

  … and 1980 more.  Use --top 1985 to see all, or --min-score 0.8 to filter.
```

### Health signal

The first line after the filename is a quick health assessment:

| Axes found | Signal | Meaning |
|---|---|---|
| ≥ 1,000 | ✓ Genuine calibration binary | Expected for a real ECU. |
| 100–999 | ⚠ Few axes | Possibly corrupted, trimmed, or a partial dump. |
| < 100 | ✗ Very few axes | Likely encrypted, non-ECU, or empty. |

---

## JSON output

```bash
openremap scan-maps ecu.bin --json --top 5
```

```json
{
  "file": "ecu.bin",
  "file_size": 4194304,
  "axes_count": 15967,
  "tables_count": 1985,
  "layout_filtered": true,
  "tables_hidden": 304,
  "tables": [
    {
      "offset": 227058,
      "cols": 32,
      "rows": 16,
      "cell_width": 2,
      "byte_order": "little",
      "x_axis_offset": 226962,
      "y_axis_offset": 227026,
      "score": 0.977
    }
  ]
}
```

`layout_filtered` is true when the calibration-region default applied;
`tables_hidden` counts tables outside it.  With `--whole-file` both are
`false` / `0`.  Axes are capped at 200 in JSON output to keep the payload
reasonable.

---

## Notes

- The scanner is **structural, not semantic** — it looks for byte patterns
  that look like calibration axes and tables. It does not know whether a
  found table is fuel, timing, boost, or EGR. You interpret what you find.
- The scanner only detects **consecutive** `[X axis][Y axis][data]` layouts.
  Some ECUs share one axis across multiple data blocks; these shared-axis
  layouts are not currently detected.
- Runtime is ~0.5–3 seconds for a typical 1–4 MB binary.
- Offsets in JSON output are always absolute file offsets, regardless of
  `--region`.

---

## Related commands

| Command | Reference |
|---|---|
| `openremap identify` | [→ identify.md](../identify/index.md) — manufacturer and SW identification |
| `openremap cook` | [→ cook.md](../cook/index.md) — diff and build a tuning recipe |

---

