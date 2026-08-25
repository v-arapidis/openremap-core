---
title: analyze — advanced
description: Describe a whole ECU binary — every flag, JSON schema, section order, speed budget.
---

# `openremap analyze`

Describe a whole ECU binary in one pass:

```bash
openremap analyze <FILE> [--json] [-o OUT] [--fast] [--no-maps]
```

Composes the existing domain services (identity, VIN, layout, maps,
checksums, health) — no new detection logic, purely descriptive.  This is
the "reverse-engineering mode" from the roadmap whose prototype was
`openremap health`.

## Flags

| Flag | Effect |
|---|---|
| `--json` | Sectioned JSON report (the full "ecu object"). |
| `-o, --output` | Save the report to a file (human output has colours stripped). |
| `--fast` | Skip maps, checksums, and the health verdict — ~1–2 s. |
| `--no-maps` | Skip only the map scan (~6 s saved), keep checksums + health. |

## Section order (human output)

1. **Container / size / SHA-256** — `raw binary` / `Intel HEX` /
   `Motorola S-Record` (content-sniffed), byte size, hash prefix.
2. **Identity** — manufacturer, family, variant, SW/HW, calibration ID,
   match key, byte order, cell size.
3. **Confidence** — tier, top-3 signal summary, warnings.
4. **VIN candidate** — only when a ≥ 0.6 candidate exists; decoded make /
   country / year labelled *decoded, unverified*.
5. **Flash layout** — segmented regions (kind, offset, size, table count)
   and the first few ident blocks.
6. **Maps** — axis/table counts + top 5 tables by score.
7. **Checksums** — ME7 verdict, Denso table, swept schemes (or "none").
8. **Health** — the six checks (identity, checksums, axis sanity, map
   count, erased blocks, VINs) with ✓ / ⚠ / ✗ / – marks.

## JSON schema

```json
{
  "container": "raw binary",
  "file_size": 4194304,
  "sha256": "00f727e8…",
  "identity": { "manufacturer": "Bosch", "ecu_family": "EDC17", "match_key": "…", "ecu_endian": "little", "ecu_cell_bytes": 2, "…": "…" },
  "confidence": { "score": 45, "tier": "Medium", "signals": [{"delta": 30, "label": "…"}], "warnings": [] },
  "vin": null,
  "hardware": { "endian": "little", "cell_bytes": 2 },
  "layout": { "regions": [{"kind": "calibration", "start": 131072, "end": 262144, "size": 131072, "confidence": 0.9, "tables": 42, "tables_high_conf": 40}], "ident_blocks": [{"start": 48284, "end": 48295}] },
  "maps": { "axis_count": 15967, "table_count": 2632, "tables": [{"offset": 2252562, "cols": 16, "rows": 16, "cell_width": 2, "byte_order": "little", "score": 0.97, "stride": null}] },
  "checksums": { "schemes": [], "me7": null, "denso": null },
  "health": { "checks": [{"name": "identity", "status": "ok", "message": "Bosch EDC17"}], "healthy": true },
  "fast": false
}
```

`maps.tables` is capped at the 50 highest-scoring tables (the full count is
in `table_count`).  `checksums` / `health` are `null` in `--fast` mode.

## Speed budget (4 MB pair)

| Mode | Sections skipped | Wall time |
|---|---|---|
| full | none | ~14 s (maps ~6 s, checksums + health ~9 s, overlapping) |
| `--no-maps` | maps | ~9 s |
| `--fast` | maps + checksums + health | ~1 s |

`analyze` is descriptive — exit code `0` even when the health verdict
contains warnings.  Use `openremap health` when you need a CI-gateable
verdict.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Read / decode / analysis error |
| 2 | Input file missing (Click `exists=True`) |
