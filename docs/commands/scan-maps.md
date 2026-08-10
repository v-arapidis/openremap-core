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
| `--min-score`, `-s` | `0.75` | Minimum table score in `[0, 1]`. Higher = fewer false positives. |
| `--region`, `-r` | (whole file) | Restrict scanning to a byte range: `0xSTART-0xEND` or `START-END`. |
| `--json` | off | Output as JSON instead of human-readable text. |

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
| ≥ 0.85 | High confidence — almost certainly a real calibration map. |
| 0.75–0.85 | Plausible — likely genuine, worth investigating. |
| 0.60–0.75 | Weak — may be coincidental structure. Lower `--min-score` if exploring. |
| < 0.60 | Noise — nearly always false positives from code or pointer tables. |

The default `--min-score` of `0.75` filters out most coincidental patterns.
Lower it to `0.55` for exhaustive scanning of unsupported ECUs; raise to
`0.85` for high-confidence maps only.

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

Axes are capped at 200 in JSON output to keep the payload reasonable.

---

## Notes

- The scanner is **structural, not semantic** — it looks for byte patterns
  that look like calibration axes and tables. It does not know whether a
  found table is fuel, timing, boost, or EGR. You interpret what you find.
- The scanner only detects **consecutive** `[X axis][Y axis][data]` layouts.
  Some ECUs share one axis across multiple data blocks; these shared-axis
  layouts are not currently detected.
- Runtime is ~0.5–2 seconds for a typical 1–4 MB binary with the Rust
  backend (`openremap --version` shows `(rust)` when active).
- Offsets in JSON output are always absolute file offsets, regardless of
  `--region`.

---

## Related commands

| Command | Reference |
|---|---|
| `openremap identify` | [→ identify.md](identify.md) — manufacturer and SW identification |
| `openremap cook` | [→ cook.md](cook.md) — diff and build a tuning recipe |

---

← [Back to CLI reference](../cli.md)
