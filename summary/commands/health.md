# `health` — command summary (fast-lookup)

> One-file reference for `openremap health <file> [--json] [--output PATH]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap health <FILE> [--json] [--output PATH]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="health", ...)`
  → `openremap/core/cli/commands/health.py::health_cmd()`.
- Argument `file`: `exists, file_okay, readable, resolve_path` (dir_okay=False) —
  a missing file exits **2**.
- `--json` for machine consumption; `--output` writes the JSON report to a file
  (parent dirs created).  The exit code is a **CI gate**: 0 = healthy, 1 = at least
  one check failed.

## Flow (top → bottom)

1. **Read + decode** — `cli/io.py::load_binary_file(file, "Binary")` → `read_bytes`
   → `services/convert.py::decode_image` (content-sniffs Intel HEX / S-Record via
   bincopy; raw passes through).  Read/decode/empty errors → styled stderr + exit **1**.
2. **Report** — `services/health.py::health_report(data, file.name)`:
   - `identify/identifier.py::identify_ecu(data, filename)` → family / manufacturer.
   - `identify/confidence.py::score_identity(ident, filename=filename, data=data).tier`
     → confidence tier (High/Medium/Low/Suspicious).
   - Six checks appended in order:
     1. `_check_identity` — `identify_ecu` again (2nd call); **warn** when family is
        None ("unidentified binary — family-specific checks will be skipped").
     2. `_check_checksums` — `checksums/checksum.py::verify_me7`,
        `detect_me7_multipoint`, `detect_me7_multipoint_unverified`,
        `checksums/nefmoto.py::detect_me7_rolling`, `detect_me7_multirange`,
        `checksums/ms43.py::detect_ms43`, `checksums/denso.py::detect_denso`,
        `checksums/ironfelix.py::detect_all` — **fail** when any detected scheme is
        STALE; **skip** when no scheme detected.
     3. `_check_axis_sanity` — `maps/map_hunter.py::scan_map_tables(data)`, u16 axis
        reads (`_read_axis`), `maps/map_classifier.py::family_fuel_type(family)` for
        the diesel RPM cap; warn-only (axis max ≥30000 suspicious, ≥60000 garbage,
        diesel >9000).
     4. `_check_map_count` — high-score (≥0.85) table count vs the corpus-derived
        `_MAP_ENVELOPES` (longest-matching family prefix — EDC15 must not match
        EDC1); **fail** below/above the envelope (wiped calibration / scanner
        garbage); **skip** when family unknown or no envelope.
     5. `_check_erased_blocks` — `maps/layout.py::segment(data)`; large (≥0x4000)
        erased regions embedded mid-file (start > 0x1000 and end < len−0x1000) →
        **warn** (file-start/end erasure is normal flash layout).
     6. `_check_vins` — `identify/vin_scanner.py::scan_vins(data,
        min_confidence=0.6)` + `services/vin_decode.py::decode_vin`; >1 distinct VIN
        → **warn** (clone/merge artifact).
   - `healthy` ⇔ no check has `status == "fail"` (warn/skip never block).
3. **Render** — `--json`: `{file, file_size, family, manufacturer, confidence_tier,
   healthy, checks[{name, status, message, details[]}]}` via stdlib `json.dumps`
   (indent=2, sorted keys); `--output` writes it (mkdir parents; write error → red
   error + exit **1**), else stdout.  Then `raise typer.Exit(code=0 if report.healthy
   else 1)`.  Human: header (`HEALTHY` green / `ISSUES FOUND` red, family + size) +
   per-check rows (`name` + coloured OK/WARN/FAIL/SKIP + message + dim details);
   exit **1** when not healthy.

## Expected output

**Human**:

```
  OpenRemap — Calibration Health
  ecu.bin  •  4,194,304 bytes  •  EDC17  •  HEALTHY
  identity       OK     Bosch EDC17
  checksums      OK     ME7 main; ME7 rolling 4/4; ...
  axis sanity    OK     154 table(s), 308 axis(es) plausible
  map count      OK     221 high-score table(s), envelope [188, 1044]
  erased blocks  OK     3 erased region(s), none embedded in data
  VINs           OK     single VIN WAUZZZ8KX9A123456 (mirrored 3x)
```

**JSON** — fields listed above; `healthy` is the gate
(`all(c.status != "fail" for c in checks)`).

**Exit codes:** `0` healthy · `1` any check failed / read-decode error / write error ·
`2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 single-file CLI commands |
| `health_report` | `core/services/health.py` | `analyze` service (verdict section; skipped in `fast` mode), `health` command |
| `identify_ecu` | `core/services/identify/identifier.py` | `health`, `analyze`, cook identity guard (`recipe_builder`), tune/validate (`preflight`), `routine`, `scan-maps`, TUI, `server` |
| `score_identity` | `core/services/identify/confidence.py` | `identify`, `health`, `analyze`, TUI, `server` |
| `scan_map_tables` | `core/services/maps/map_hunter.py` | `analyze`, `health`, `segment`, `recipe_maps`, `recipe_regions`, `cook`, `cook-volatile`, `scan-maps` |
| `segment` | `core/services/maps/layout.py` | `health`, `analyze`, `layout` command, `recipe_regions`, `audit`, `scan-maps`, `arch/pseudocode` |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `identify`, `health`, `analyze`, cook annotator (`annotator.py`), `cook-volatile` (`volatile.py`), `scan-vins` |
| `decode_vin` | `core/services/vin_decode.py` | `identify`, `health`, `analyze`, `scan-vins` |
| `family_fuel_type` | `core/services/maps/map_classifier.py` | `health`, `recipe_maps` (`attach_maps`), `scan-maps` |
| checksum detectors (`verify_me7`, `detect_me7_*`, `detect_ms43`, `detect_denso`, `detect_all`) | `core/services/checksums/` | `checksum` command, `analyze` service, `cook-volatile` (`volatile.py`), `health` |

## Gotchas

- `healthy` ignores **warn** and **skip** — only a `fail` flips the CI gate.
- `--output` is silently ignored in human mode (it is only consulted inside the
  `--json` branch) despite the help text claiming "--output requires --json" —
  `--output` without `--json` writes nothing and exits normally.
- The map-count envelope uses the **longest matching family prefix** and is
  corpus-derived (measured 2026-08-15); families without a measured envelope
  `skip` honestly — changing `_MAP_ENVELOPES` re-gates every consumer.
- `_check_checksums` runs **every** known scheme on every file (not family-gated) —
  the slowest check; scheme detection order decides which schemes are reported.
- The report calls `scan_map_tables` up to **three times** per file (axis sanity,
  map count, and `segment` internally for erased blocks) and `identify_ecu` **twice**
  (`health_report` + `_check_identity`) — an expensive pass on 4 MB dumps.
