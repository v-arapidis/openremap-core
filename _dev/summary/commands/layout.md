# `layout` — command summary (fast-lookup)

> One-file reference for `openremap layout <file> [--json] [--min-run N]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap layout <FILE> [--json] [--min-run N]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="layout",
  help="Print the flash-layout block map …")` (main.py:165) →
  `openremap/core/cli/commands/layout.py::layout()`.
- Argument `file`: `exists, file_okay, dir_okay=False, readable,
  resolve_path` (missing file → typer exit **2**).
- Options: `--json` (JSON output) · `--min-run N` (minimum printable-ASCII
  run length for ident-block detection, default **64**).

## Flow (top → bottom)

1. **Read + decode** — `cli/io.py::load_binary_file(path, "Binary")`
   (content-sniffs Intel HEX / Motorola S-Record via `decode_image`; raw
   dumps pass through).  Read/decode error → typer error + exit **1**.
2. **Segment** — `core/services/maps/layout.py::segment(data)` → the region
   list.  **Data-driven, no manufacturer database**; kinds are
   probabilistic labels with a `confidence` column:
   - `erased` — one repeated byte (`FF`/`00`/… family-specific erase byte)
   - `code` — busy data with no calibration maps
   - `calibration` — dense with high-score maps (RPM×Load tables)
   - `mixed` — no decisive signal (low confidence)
   - `ident` — readable ASCII metadata block (exact byte range)
3. **Ident blocks** — `find_ident_blocks(data, min_run=min_run)` → exact
   byte ranges of readable ASCII metadata (honours `--min-run`).
4. **Render** — human: coloured table (Start/End/Size/Kind/Fill/Ent/Tbls/
   Conf) one line per region, then one per ident block; JSON: `{file,
   file_size, regions[], ident_blocks[]}` via `json.dumps(indent=2,
   ensure_ascii=False)`.

## Expected output

**Human** (colours stripped when writing to a file):

```
  OpenRemap — Flash-Layout Segmentation
  <name>  •  <size> bytes  •  N region(s)  •  M ident block(s)

    Start     End      Size        Kind  Fill   Ent   Tbls   Conf
  ──────────────────────────────────────────────────────────────────────────
  0x000000  0x07FFFF  524,288    erased  0xFF  0.00      0  1.00
  0x080000  0x3FFFFF  3,670,016     code   —   5.42      0  0.87
  ...
  0x7F0000  0x7FFFFF  65,536  calibration   —   6.10     12  0.94
  0xFFB80   0xFFBFF  ident block  …
```

**JSON** — `{file, file_size, regions: [{start, end, size, kind,
fill_byte, fill_ratio, mean_entropy, tables_high_conf, confidence}],
ident_blocks: [{start, end, size}]}`.

**Exit codes:** `0` ok · `1` read/decode error · `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all bin-reading CLI commands + TUI |
| `segment` | `core/services/maps/layout.py` | `analyze`, `scan_maps`, `health`, `recipe_regions`, `pseudocode`, `audit` (via its own import) |
| `find_ident_blocks` | `core/services/maps/layout.py` | `analyze`, `vin_scanner`, `confidence`, `audit`, `volatile` |

## Gotchas

- Kinds are **probabilistic labels, not facts** — `mixed` = no decisive
  signal; a `code` region may still contain data.
- `--min-run` affects **only** ident-block detection, never the segment
  kinds.
- `--json` emits the full region list — large files produce large output.
- `segment`/`find_ident_blocks` are shared with `analyze`, `health`, the
  recipe services (`audit`, `volatile`, `recipe_regions`) and the arch
  `pseudocode` renderer — a segmentation change ripples across all of them.
