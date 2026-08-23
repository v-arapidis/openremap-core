---
title: health — advanced
description: One-shot calibration health check — every flag, JSON gate, examples.
---

# `openremap health`

One-shot calibration health check for a single ECU binary — the "check
engine light" for a ROM file.  Runs every analysis layer once and reports
each concern as `ok` / `warn` / `fail` / `skip`.

Exit code `0` = healthy, `1` = at least one check failed — usable as a CI
gate.

## Usage

```
openremap health ecu.bin
openremap health ecu.bin --json
openremap health ecu.bin --json --output report.json
```

## Checks

| Check | What it looks at | Verdict |
|---|---|---|
| `identity` | family / manufacturer / confidence tier | `warn` when unidentified |
| `checksums` | every known family scheme (ME7 main/multipoint/rolling/multirange, MS43, Denso descriptor table, IronFelix profiles) | `fail` when any detected scheme is STALE |
| `axis sanity` | axes of high-score tables (implausible values, FF-fill, diesel caps) | `warn` only — scanner artifacts on healthy files are expected; corruption is caught by map count |
| `map count` | high-score table count vs a corpus-derived envelope per ECU family | `fail` below/above the envelope (wiped calibration / scanner garbage) |
| `erased blocks` | large erased regions embedded in data | `warn` — normal for some layouts (Subaru bank mirrors); verify against a known-good dump otherwise |
| `VINs` | distinct high-confidence VINs in one file | `warn` on duplicates (cloning/merge artifact) |

A file is **healthy** iff no check fails; `warn` levels are reported but
do not block the gate.

## CI gating

```bash
openremap health stock.bin --json --output stock.json
test "$(python -c "import json;print(json.load(open('stock.json'))['healthy'])")" = "True"
```

## Notes

- The per-family map-count envelopes are **corpus-derived** (measured
  2026-08-15 on `tests/data/ECUs`, families without a measured envelope
  skip the check honestly).  See `openremap/core/services/health.py`.
- **Denso diesel factory files report `checksums: fail` by design**: one
  descriptor-table entry covers the runtime-patched tail and is stale in
  every factory dump (see the [checksum command docs](../checksum/advanced.md)).  Verify other entries
  when in doubt.
- Some families (MS43, GS20/SMG2 TCUs) have checksum profiles but **no
  extractor** — their identity check reports `warn` (unidentified) while
  the checksum check still runs.
- `health` is the first cross-domain consumer: identity + checksums +
  map scanning + layout segmentation + VIN scanning in one pass — the
  prototype of a unified `analyze` model.
