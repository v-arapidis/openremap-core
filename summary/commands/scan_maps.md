# `scan-maps` — command summary (fast-lookup)

> One-file reference for `openremap scan-maps <file|dir> [--json] [--top N]
> [--min-score F] [--region RANGE] [--whole-file] [--export DIR] [--classify]
> [--xrefs]`: entry point, exact call flow, expected output, and every shared
> function it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap scan-maps <FILE|DIR> [--top N] [--min-score F] [--region RANGE]
  [--whole-file] [--json] [--max-series-tables N] [--show-series] [--export DIR]
  [--recursive/-R] [--verbose/-v] [--classify] [--xrefs]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="scan-maps", ...)`
  → `openremap/core/cli/commands/scan_maps.py::scan_maps()` (underscore module/function,
  hyphenated CLI name).
- Argument `path`: `exists, file_okay, dir_okay, readable, resolve_path` — accepts a
  single file OR a directory; a missing path exits **2**.
- Key options: `--top` (default 20), `--min-score` (0.85), `--region` (hex
  `START-END`/`0xSTART-0xEND`), `--whole-file`, `--json`, `--max-series-tables` (16),
  `--show-series`, `--export DIR`, `--recursive`, `--verbose`, `--classify`, `--xrefs`.

## Flow (top → bottom)

1. **Single file** (`path.is_file()`): `_read_bin` gates the extension
   (`VALID_EXTENSIONS` = {.bin,.ori,.hex,.s19,.srec,.mot}; bad suffix → red error +
   `typer.Exit(1)`), then `cli/io.py::load_binary_file(path, "Binary")` → `read_bytes`
   → `services/convert.py::decode_image` (content-sniffs Intel HEX / S-Record via
   bincopy; raw passes through).  Read/decode/empty errors → styled stderr + exit **1**.
2. `_parse_region(region)` — hex `0xSTART-0xEND` / `START-END` (also `..` separator)
   → Python `slice`; parse errors or end < start → red error + exit **1**.
3. `_scan_one(data, region_slice, min_score, max_series_tables, layout_default=not whole_file)`:
   - `maps/map_hunter.py::scan_map_axes(data, region=region_slice)` → Rust
     `openremap._rust.scan_map_axes` → `list[MapAxis]` (offset, length, byte_order,
     values).
   - `maps/map_hunter.py::scan_map_tables(data, region, axes, min_score,
     max_series_tables)` → Rust `openremap._rust.scan_map_tables` → `list[MapTable]`
     (sorted score desc).
   - `top_score = max(t.score, default=0.0)`.
   - Default (no `--region`, no `--whole-file`): `_apply_calibration_filter` →
     `_calibration_spans` → `maps/layout.py::segment(data, tables=result["tables"])`
     (64 KB / 16 KB sectors; a sector with ≥1 table scored ≥0.85 → `calibration`).
     Tables outside the calibration spans are dropped and counted in
     `tables_hidden`; `layout_filtered=True`.  **Axes are NOT filtered** — the
     axes-count health signal keeps its whole-file meaning.  No calibration signal →
     result left untouched (whole-file fallback).
4. **`--classify`** → `_classify_for_file`: best-effort
   `identify/identifier.py::identify_ecu(data, filename="<scan>")` (any exception →
   family=None) → `maps/map_classifier.py::family_fuel_type(family)`
   (diesel/petrol/None) → `maps/map_classifier.py::classify_tables(data, tables,
   fuel_type)` → `{offset: (label, confidence)}` (fuel/timing/boost/torque/duration/
   unknown).
5. **`--xrefs`** → `_xref_for_file`:
   - `identify_ecu(data, filename="<scan>")` (exception → `(tables, None)`).
   - `arch/__init__.py::arch_for_family(manufacturer, ecu_family)` — family-prefix
     table (TriCore/SuperH/c166/8051/mcs96/M680X/68K/PPC); `None` for unmapped
     families.
   - `segment(data, tables)` → `maps/layout.py::code_regions_from_layout(regions)`;
     no code regions → `(tables, None)`.
   - `maps/xrefs.py::_table_spans(tables)` → table data spans.
   - **Known family** → `arch/refs.py::collect_xrefs(data, codes, arch, ecu_endian,
     spans=spans)`; **unknown family** → `arch/detect.py::detect_arch(data, codes,
     ecu_endian, spans)` (CPU-detection cascade, see Gotchas).
   - `xr.status != "ok"` → report returned as-is; else
     `maps/xrefs.py::adjust_table_scores(tables, xr)` (+0.06 score bonus to
     data-referenced tables, re-sorted) and `top_score` recomputed.
6. **Render** — `--json`: `_build_json_result` (axes capped at 200 with `values[:16]`;
   tables capped at `--top`; per-table `xref` evidence via `maps/xrefs.py::xref_evidence`
   + an `xrefs` summary block) → `json.dumps(indent=2)` to stdout.  Human:
   `_print_single_result` — axes-count health badge (≥1000 ✓ genuine / ≥100 ⚠ few /
   else ✗), table listing with `maps/xrefs.py::data_refs_for_table` "⟶code" cyan marks
   and classifier label cells (green ≥0.7 / yellow ≥0.45), "… and N more" hint.
7. **`--export DIR`** → `maps/map_exporter.py::export_tables_csv(data, tables[:top],
   dir)` → WinOLS-grid CSV files; count echoed in green.
8. **Directory mode**: `_collect_candidates(directory, recursive)` — suffix filter on
   `VALID_EXTENSIONS`, sorted; none → yellow note + normal return (exit 0).  Per file:
   `read_bytes` (OSError → `READ ERR` row) → `decode_image(data).data` (ValueError →
   `READ ERR` row) → empty check (`EMPTY` row) → `_scan_one` (+ `--xrefs`/`--classify`)
   → health bucket (`genuine`/`few`/`sparse` by axes_count) → one-line summary
   (`[i/N] badge name axes • tables [hidden] • top • size • ms`), `--verbose` full
   listing, JSON accumulation.  Batch JSON: `{directory, files_scanned, health,
   results[], errors[]}`; human: header + `── Summary ──` counts + export total.

## Expected output

**Human** (single file):

```
  <file.bin>

  ✓  Genuine calibration binary
  1,234 axes  •  56 tables  •  4,194,304 bytes

      Offset        Dim   Cells    Score              X Axis    Y Axis
  ──────────────────────────────────────────────────────────────
  0x000376F2   32×16   u16 LE    0.912  0x00036E00  0x000376C0   ⟶code
```

**JSON** (single file) — `file, file_size, axes_count, tables_count, top_score,
layout_filtered, tables_hidden` + `axes[]` (`offset, length, byte_order, values[:16]`)
+ `tables[]` (`offset, cols, rows, cell_width, byte_order, x_axis_offset,
y_axis_offset, stride, score, label, label_confidence, xref`) + optional `xrefs`
(`status, skip_reason, arch, base_address, code_bytes_scanned, insn_count,
reference_count`).  Directory mode wraps in `{directory, files_scanned, health,
results, errors}`.

**Exit codes:** `0` ok · `1` read/decode/extension/region error · `2` missing path.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 single-file CLI commands (`identify`, `analyze`, `layout`, `diff_maps`, `tune`, `health`, `scan-vins`, `merge`, `audit`, `validate`, `cook`, `checksum`, `routine`, `scan-maps`) |
| `decode_image` | `core/services/convert.py` | batch loops: `scan`, `scan-maps` dir mode, TUI scan/cook/tune; `convert` command; `load_binary_file` |
| `scan_map_axes` | `core/services/maps/map_hunter.py` | `analyze` service, `scan-maps`, `server` (deprecated) |
| `scan_map_tables` | `core/services/maps/map_hunter.py` | `analyze` service, `health`, `layout.py::segment`, `recipe_maps` (`attach_maps`), `recipe_regions`, `cook`, `cook-volatile`, `server`, corpus scripts |
| `segment` | `core/services/maps/layout.py` | `health`, `analyze`, `layout` command, `recipe_regions`, `audit`, `arch/pseudocode`, `scan-maps` |
| `identify_ecu` | `core/services/identify/identifier.py` | `health`, `analyze`, cook identity guard (`recipe_builder`), tune/validate (`preflight`), `routine`, `scan-maps`, TUI, `server` |
| `arch_for_family` | `core/arch/__init__.py` | `analyze` (service + command), `routine`, `coherence`, `scan-maps`, corpus scripts/tests |
| `detect_arch` | `core/arch/detect.py` | `analyze` service, `scan-maps`, `scripts/census_arch.py` |
| `collect_xrefs` | `core/arch/refs.py` | `analyze` service, `detect_arch` (trial decodes), `scan-maps`, corpus tests |
| `adjust_table_scores` / `_table_spans` / `xref_evidence` / `data_refs_for_table` | `core/services/maps/xrefs.py` | `analyze` service, `recipe_maps` (`attach_maps`), `scan-maps` |
| `classify_tables` / `classify_table` / `family_fuel_type` | `core/services/maps/map_classifier.py` | `recipe_maps` (`attach_maps` label layer), `health` (`family_fuel_type`), `scan-maps` |
| `export_tables_csv` | `core/services/maps/map_exporter.py` | `scan-maps` only |
| `_scan_one` / `_parse_region` | `cli/commands/scan_maps.py` | **`diff_maps` imports both directly** |

## Gotchas

- The default scan is **region-filtered, not whole-file**: `layout_default=not
  whole_file` hides tables outside the detected calibration region (`tables_hidden`
  count; human hint "use --whole-file").  `--region` overrides the filter entirely.
- `--xrefs` silently yields no signal when identity fails, no code regions exist, or
  the arch pass skips (`xr.status != "ok"`) — presence-only contract: absence never
  demotes a table, it just renders without the `⟶code` mark.
- The CPU-detection cascade (`detect_arch`) for unknown families is **capstone-first —
  TriCore, then SuperH SH-2 → SH-2A — with c166 LAST**, and c166 is additionally
  gated on a **boot DPP init** (`c166.find_dpp_init`); x86 is deliberately excluded.
  Every trial decode runs in a **forked child** (`_trial_collect`) so a capstone
  C-level segfault (e.g. the SH-2A out-of-bounds read, GHSA-gf2c-xwcp-hvf4) rejects
  that candidate instead of killing the process.  A wrong arch whose garbage
  references happen to hit spans could in principle false-positive — bounded by
  `_accepts` (status ok, ≥50 insns, ≥3 refs inside the data spans).
- `_collect_candidates` docstring says ".bin/.ori" but the code filters on the full
  `VALID_EXTENSIONS` set (includes .hex/.s19/.srec/.mot).
- `--export` writes **flat into `--export`** in single-file mode but creates one
  sub-folder **per file stem** (`<stem>_maps/`) in directory mode.
- The axes-count health signal is whole-file even when tables are filtered — axes are
  deliberately never region-filtered (changing that breaks the health semantics).
- `diff_maps` reuses `_scan_one`/`_parse_region` — changing their signature or result
  shape breaks `openremap diff-maps` too.
