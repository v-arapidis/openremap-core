# `identify` — command summary (fast-lookup)

> One-file reference for `openremap identify <file> [--json] [-o out]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap identify <FILE> [--json] [--output PATH]`
- Registered in `openremap/cli/main.py` via `app.command(name="identify")`
  → `openremap/cli/commands/identify.py::identify()`.
- Argument `file`: `exists, file_okay, readable, resolve_path` (Click enforces
  existence → missing file exits **2**).

## Flow (top → bottom)

1. **Extension check (advisory only)** — unknown suffix prints a yellow
   warning and proceeds; `.bin/.ori/.hex/.s19/.srec/.mot` accepted.
2. **Read + decode** — `cli/io.py::load_binary_file(path, "Binary")` →
   `core/services/convert.py::decode_image(raw)` (content-sniffs Intel
   HEX/S-Record via bincopy; raw dumps pass through).  Read/decode/empty
   errors → styled stderr + exit **1**.  The returned format code is shown
   as **Container** (`Intel HEX` / `Motorola S-Record` / `raw binary`).
3. **Identity** — `core/services/identify/identifier.py::identify_ecu(data,
   filename)`:
   - `_detect_endian` → Rust `openremap._rust.detect_endian` (hardware prop).
   - `manufacturers/__init__.py::get_extractors()` — ordered registry
     **Bosch → Siemens → Delphi → Marelli → Denso → Hitachi**; intra-brand
     order owned by each brand package.
   - **First match wins**: iterate; first `extractor.can_handle(data)`
     → `extractor.extract(data, filename)`
     (`core/manufacturers/base.py::BaseManufacturerExtractor` ABC;
     `_set_evidence()` tags collected during `can_handle`; `detection_strength`
     class attr).  None match → `_unknown_identity()` (all fields None).
   - `_to_identity(rich, size, sha256, strength, evidence, endian, cell)`
     → lean identity dict.
4. **Confidence** — `core/services/identify/confidence.py::score_identity(
   result, filename, data)` → `ConfidenceResult(score, tier, signals,
   warnings)`.  Tiers: `score ≥55` High, `≥25` Medium, `≥0` Low, `<0`
   Suspicious.  Evidence tags → dynamic bonus (supersedes static
   `detection_strength`); `data` given → ident-block cross-check.
5. **VIN candidate (floor 0.6, display-only)** — `core/services/identify/
   vin_scanner.py::scan_vins(data, min_confidence=0.6)`; top hit →
   `core/services/vin_decode.py::decode_vin(vin)` (vininfo, never raises).
   **Never** affects match key or confidence.
6. **Render** — human: `_format_table` (`_LABELS` rows) + Confidence section
   (tier, top-3 signal summary, warnings) + optional `── VIN candidate ──`
   section.  JSON: `dict(result)` + `confidence{score,tier,signals,warnings}`
   + `vin` (object | `null`) via stdlib `json.dumps`.  `_write_output`
   (file with mkdir, or stdout); write error → exit **1**.

## Expected output

**Human** (colours stripped when `--output` writes a file):

```
  <filename>
  Bosch · EDC17
  Container         Intel HEX | Motorola S-Record | raw binary
  Manufacturer      Bosch
  ECU Family        EDC17
  ...
  Match Key         EDC17C66::1037541778126241V0
  Byte Order        little-endian
  Cell Size         16-bit
  File Size         4,194,304 bytes
  SHA-256           <hex>
  ── Confidence ── ...
  ── VIN candidate ── ...            (only when a ≥0.6 candidate exists)
```

**JSON** — identity fields (`manufacturer, match_key, ecu_family,
ecu_variant, software_version, hardware_number, calibration_id,
oem_part_number, detection_strength, detection_evidence, file_size, sha256,
md5, calibration_version, sw_base_version, serial_number, dataset_number,
raw_strings, ident_block, ecu_endian, ecu_cell_bytes`) + `container`
(`raw binary` / `Intel HEX` / `Motorola S-Record`) + `confidence` +
`vin` (`null` when no candidate ≥ 0.6; else `{candidate, confidence,
manufacturer, region, country, years, checksum_valid, decoded}`).

**Exit codes:** `0` ok · `1` read/decode/identify/write error · `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 13 bin-reading CLI commands + TUI (via `decode_image`) |
| `decode_image` | `core/services/convert.py` | batch loops: `scan`, `scan_maps` dir, TUI scan; `convert` command |
| `identify_ecu` | `core/services/identify/identifier.py` | `health`, `scan_maps`, cook identity guard (`recipe_builder`), tune/validate (`preflight`), TUI, `server` (deprecated) |
| `score_identity` | `core/services/identify/confidence.py` | `scan`, `health`, TUI, `server` |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `scan-vins`, `health`, cook annotator (`annotator.py`), `cook-volatile` (`volatile.py`) |
| `decode_vin` | `core/services/vin_decode.py` | `scan-vins`, `health` |
| `get_extractors` / `BUILTIN_EXTRACTORS` | `core/manufacturers/__init__.py` | `scan`, `health`, TUI, cook identity match |

## Gotchas

- Registry **order = detection priority** (first `can_handle()` wins) —
  changing `BUILTIN_EXTRACTORS` reorders detection globally.
- The extension check is **warn-only**; `.hex` may be a raw Subaru dump,
  not Intel HEX text (content sniff decides).
- VIN output is additive/display-only — consumers of `--json` get a new
  `vin` key (nullable); `match_key` semantics unchanged.
