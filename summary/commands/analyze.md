# `analyze` — command summary (fast-lookup)

> One-file reference for `openremap analyze <file> [--json] [-o out]
> [--fast] [--no-maps]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Registered in `openremap/cli/main.py` via `app.command(name="analyze")`
  → `openremap/cli/commands/analyze.py::analyze()`.
- Flags: `--json`, `--output/-o`, `--fast` (skip maps+checksums+health),
  `--no-maps` (skip map scan only).  Argument `file`: `exists,
  file_okay, readable, resolve_path` (missing → exit **2**).

## Flow (top → bottom)

1. **Read + decode** — `cli/io.py::load_binary_file(path, "Binary")` →
   `core/services/convert.py::decode_image` (HEX/SREC sniff); format code →
   `cli/io.py::CONTAINER_NAMES` (shared with `identify`).
2. **Compose** — `core/services/analyze.py::analyze_binary(data, filename,
   *, fast, skip_maps, container)` → `AnalyzeReport` (one pass):
   - `identify/identifier.py::identify_ecu` → `identify/confidence.py::score_identity`
   - `identify/vin_scanner.py::scan_vins(min_confidence=0.6)` →
     `vin_decode.py::decode_vin` (mirrors `identify`)
   - maps: `maps/map_hunter.py::scan_map_axes` → `scan_map_tables` (whole
     file, per plan §4A) → `maps/layout.py::segment(data, tables=…)`
     (reuses the scan — no second pass) + `find_ident_blocks`
   - checksums: `checksums/checksum.py::sweep` + `verify_me7`,
     `checksums/denso.py::detect_denso` → compact summary dict
   - health: `health.py::health_report` (reused wholesale — re-scans
     internally; accepted double-scan for v1)
3. **Render** — human `_render()` (sectioned: container/identity/
   confidence/VIN/layout/maps/checksums/health); JSON
   `AnalyzeReport.to_dict()` via stdlib `json.dumps`.  Write via
   `_write_output`-style block; analysis exception → exit **1**.

## Expected output

**Human** — sectioned report (see `docs/commands/analyze/advanced.md`);
ident blocks capped at 8 + "… N more"; maps top-5 by score; fast-mode
warning line at the end.

**JSON** — 12 top-level keys: `container, file_size, sha256, identity,
confidence, vin, hardware, layout, maps, checksums, health, fast`.
`maps.tables` capped at 50 by score; `checksums`/`health` are `null` in
`--fast`.

**Exit codes:** `0` always on success (descriptive — warnings don't fail);
`1` read/decode/analysis error; `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` / `CONTAINER_NAMES` | `cli/io.py` | all 13 bin-reading CLI commands + TUI; `identify` |
| `decode_image` | `core/services/convert.py` | batch loops (`scan`, `scan_maps` dir, TUI scan), `convert`, `identify` |
| `identify_ecu` | `core/services/identify/identifier.py` | `identify`, `health`, `scan_maps`, cook (`recipe_builder`), tune/validate (`preflight`), TUI |
| `score_identity` | `core/services/identify/confidence.py` | `identify`, `scan`, `health`, TUI |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `identify`, `scan-vins`, `health`, cook annotator, `cook-volatile` |
| `decode_vin` | `core/services/vin_decode.py` | `identify`, `scan-vins`, `health` |
| `scan_map_axes` / `scan_map_tables` | `core/services/maps/map_hunter.py` | `scan-maps`, `diff-maps`, `cook` (attach_maps), `health` |
| `segment` / `find_ident_blocks` | `core/services/maps/layout.py` | `layout`, `scan-maps`, `diff-maps`, `cook` regions, health |
| `sweep` / `verify_me7` / `detect_denso` | `core/services/checksums/*` | `checksum`, `health` |
| `health_report` | `core/services/health.py` | `health` (CLI), `analyze` |

## Gotchas

- **`--fast` skips `health_report` entirely** — do not call it in fast mode
  (it re-runs the ~9 s scans internally and would defeat the flag).
- The map scan is whole-file (plan §4A) — tables in code/erased sectors
  are included; `scan-maps`' calibration-region default is NOT applied.
- Serialisation stays **stdlib json** (byte-stability rule) — `to_dict()`
  is the single JSON-safe conversion point.
- `analyze` is descriptive, never a gate — `health` owns exit-code
  verdicts for CI.
