---
title: layout — advanced
description: Flash-layout block map — every flag, JSON output, examples.
---

# `openremap layout`

Print the flash-layout block map of an ECU binary — where the erased pages,
code, calibration area, and ident blocks start and end.

Purely data-driven segmentation (sector entropy + fill profiles + map
density) — no manufacturer database. Kinds are **probabilistic labels**
with a confidence value, never verified names. Bootloader vs program code
is deliberately not distinguished (both are `code`).

---

## Usage

```bash
openremap layout <FILE> [--json] [--min-run N]
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--json` | off | Output as JSON instead of a table. |
| `--min-run` | `64` | Minimum printable-ASCII run length for ident-block detection. |

---

## Kinds

| Kind | Meaning | Signal |
|---|---|---|
| `erased` | Empty flash pages — one repeated byte (FF, 00, C3, …) | dominant-byte ratio ≥ 0.95 |
| `calibration` | The tunable area — dense with maps | ≥ 1 table scored ≥ 0.85 in the sector |
| `code` | Program code — busy, no maps | entropy ≥ 6.0 bits/byte, no high-score tables |
| `ident` | Ident/metadata text blocks | exact printable-ASCII runs (SW/HW numbers, VIN) |
| `mixed` | No decisive signal | fallback, confidence 0.3 |

Sector granularity: 64 KB for bins ≥ 256 KB, 16 KB below. Adjacent sectors
of the same kind merge into regions.

---

## Example

```
  OpenRemap — Flash-Layout Segmentation
  Audi A4 2.5TDI 163HP 0281012142  •  1,048,576 bytes  •  3 region(s)

     Start       End       Size          Kind    Fill    Ent   Tbls   Conf
  0x000000  0x080000   524,288        erased    0xC3   0.00      0   0.95
  0x080000  0x0C0000   262,144          code       —   6.75      0   0.70
  0x0C0000  0x100000   262,144   calibration       —   4.93     55   0.68
  0x0CC51B  0x0CC57C        97         ident       —   1.07      0   0.50
```

This EDC15 file shows the classic layout: a 0xC3-erased first half (that
family's erase byte), code in the middle, calibration + ident records on
top. The same ident record repeats across pages (Bosch mirror blocks).

---

## Related commands

| Command | Reference |
|---|---|
| `openremap scan-maps` | [→ scan-maps.md](../scan-maps/index.md) — structural map discovery |
| `openremap identify` | [→ identify.md](../identify/index.md) — manufacturer/SW identification |

---

