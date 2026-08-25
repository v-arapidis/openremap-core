---
title: scan-vins — advanced
description: Full reference — every flag, the evidence fields, and the JSON output schema.
---

# scan-vins — advanced

## Synopsis

```bash
openremap scan-vins [OPTIONS] FILE
```

## Flags

| Flag | Default | Description |
|---|---|---|
| `--min-confidence FLOAT` | `0.4` | Only show candidates with confidence >= this value (0.0–1.0) |
| `--json` | — | Output as JSON instead of a table |
| `--help` | — | Show help |

## How candidates are scored

Each candidate accumulates evidence — confidence is the weighted sum,
capped at 0.95. It is **never** a boolean claim:

| Evidence | Weight |
|---|---|
| WMI in the known-manufacturer whitelist (`WVW`, `WAU`, `WBA`, …) | +0.30 |
| ISO 3779 position-9 check digit valid | +0.25 |
| Model-year character plausible (position 10) | +0.10 |
| Positions 12–17 all digits | +0.10 |
| Candidate inside an ident block (layout segmenter) | +0.10 |
| Mirror consensus — same VIN appears multiple times | +0.10 |

Candidates with fewer than 6 distinct characters (fills like
`99999999999999999`) are rejected up front.

## JSON output

```json
[
  {
    "offset": 8192,
    "vin": "WVWZZZ1JZXW000001",
    "confidence": 0.9,
    "wmi_known": true,
    "check_digit_ok": true,
    "year_plausible": true,
    "numeric_tail": true,
    "in_ident_block": true,
    "mirror_count": 2
  }
]
```

## Examples

```bash
# Find everything (default threshold)
openremap scan-vins stock.bin

# Only strong candidates — for a cloning/merge audit
openremap scan-vins stock.bin --min-confidence 0.6 --json
```

## Notes

- Real VINs live in ident blocks and are usually mirrored; natural
  lookalikes (part numbers, serials) score ≤ 0.4 on the measured corpus.
- Two distinct high-confidence VINs in one file is the classic sign of a
  cloned or merged dump — see also `openremap health`.
- Every candidate is decoded with **vininfo** (BSD-3): the JSON carries
  `manufacturer`, `region`, `country`, `years`, `checksum_valid`, and
  `decoded`; the table appends a dim *— Make, Country, Year (decoded,
  unverified)* suffix.  Decoding is permissive — unknown WMIs yield
  `decoded: false` with no guesswork, and malformed input never errors.
