# `scan-vins` — command summary (fast-lookup)

> One-file reference for `openremap scan-vins <file> [--min-confidence F] [--json]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap scan-vins <FILE> [--min-confidence 0.0-1.0] [--json]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="scan-vins", ...)`
  → `openremap/core/cli/commands/scan_vins.py::scan_vins_cmd()`.
- Argument `file`: `exists, file_okay, readable, resolve_path` (dir_okay=False) —
  a missing file exits **2**.
- Option `--min-confidence` default **0.4** — only candidates ≥ this threshold are
  shown (the service default is 0.0; the CLI sets its own policy).

## Flow (top → bottom)

1. **Read + decode** — `cli/io.py::load_binary_file(file, "Binary")` → `read_bytes`
   → `services/convert.py::decode_image` (content-sniffs Intel HEX / S-Record via
   bincopy; raw passes through).  Read/decode/empty errors → styled stderr + exit **1**.
2. **Scan** — `services/identify/vin_scanner.py::scan_vins(data,
   min_confidence=min_confidence)`:
   - `maps/layout.py::find_ident_blocks(data)` — exact printable-ASCII runs (ident
     blocks) for the ident-block context evidence.
   - Overlapping lookahead regex `(?=([A-Z0-9]{17}))` over the whole file; skip any
     candidate containing **I/O/Q** (ISO 3779) or with **< 6 distinct chars**
     (pattern-fill guard: `99999999999999999` etc. trivially pass the checksum).
   - Evidence weights summed into confidence (cap **0.95**): WMI whitelist 0.30,
     ISO 3779 check digit 0.25 (`is_valid_check_digit`, weights+transliteration),
     model-year char 0.10, numeric tail 0.10; ident-block context +0.10 and mirror
     count +0.10 **only when the WMI is known** (serials and calibration numbers
     also live in ident blocks and are mirrored — without the gate they cross the
     lookalike line; corpus: `31011118777544444` scores 0.65 with ident+mirror,
     0.45 without).
   - Sorted by `(-confidence, offset)`; hits below `min_confidence` dropped.
3. **Decode each hit** — `services/vin_decode.py::decode_vin(h.vin)` (vininfo; never
   raises; malformed VIN or unknown/`UnsupportedBrand` WMI → `decoded=False`, all
   optional fields None).
4. **Render** — `--json`: `{file, file_size, candidates[{offset, vin, confidence,
   evidence[], mirror_count, manufacturer, region, country, years, checksum_valid,
   decoded}]}` via stdlib `json.dumps` (indent=2, sorted keys).  Human: header
   (`OpenRemap — VIN Scan`, file name • bytes • candidate count ≥ threshold),
   aligned table (`Offset` / `VIN` / `Conf` / `Evidence`) with green bold ≥0.6,
   yellow below, plus a dim `— <manufacturer>, <country>, <year> (decoded,
   unverified)` suffix when decoded; "No VIN candidates found" line when empty.

## Expected output

**Human**:

```
  OpenRemap — VIN Scan
  ecu.bin  •  4,194,304 bytes  •  3 candidate(s) ≥ 0.4

     Offset   VIN                 Conf   Evidence
  ──────────────────────────────────────────────────────────────
  0x0010A8  WAUZZZ8KX9A123456   0.90   wmi, check-digit, year, numeric-tail, ident-block, mirrored-x3  — Audi, Germany, 2009 (decoded, unverified)
```

**JSON** — per-candidate fields listed above (`evidence` is the string list from
`VINHit.evidence`; the `manufacturer/region/country/years/checksum_valid/decoded`
group comes from `decode_vin`).

**Exit codes:** `0` ok · `1` read/decode error · `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 single-file CLI commands |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `identify` (VIN candidate section), `health` (VIN-duplication check), `analyze` service, cook annotator (`annotator.py`), `cook-volatile` (`volatile.py`), `scan-vins` |
| `decode_vin` | `core/services/vin_decode.py` | `identify`, `health`, `analyze`, `scan-vins` |
| `find_ident_blocks` | `core/services/maps/layout.py` | `identify` confidence cross-check (`confidence.py`), `volatile.py`, `audit`, `layout` command, `scan-vins` |

## Gotchas

- Confidence is a **probability-style score, never a boolean claim** — ECU files are
  full of VIN-shaped serials and calibration IDs.  The CLI default floor is 0.4,
  while `identify`/`health` use 0.6 for their display/duplication candidates — the
  same service, three different thresholds.
- Ident-block and mirror evidence are **gated behind a known WMI** — touching that
  gate in `vin_scanner.py` changes every consumer's candidate list, not just this
  command's.
- The 17-char scan is **overlapping** (regex lookahead) — a VIN may start at any
  offset inside an alphanumeric run; a non-overlapping `finditer` would skip it
  (2026-08-20 fix).
- `decode_vin`'s manufacturer comes from vininfo's community database — the command
  labels it "decoded, unverified", never fact.
