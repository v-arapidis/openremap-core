# `diff-maps` — command summary (fast-lookup)

> One-file reference for `openremap diff-maps <stock.bin> <tuned.bin>
> [--threshold N] [--top N] [--json] [-v] [--export DIR] [--recipe F] [--annotate F]
> …`: entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all consumers).
> Keep this file updated when the command or its dependencies change.

## Entry & registration

- Command: `openremap diff-maps <STOCK> <TUNED> [options]` — scan both
  binaries for calibration tables, match by axis fingerprint, diff
  cell-by-cell.
- Registered in `openremap/core/cli/main.py` via
  `app.command(name="diff-maps")(diff_maps)` → CLI function
  `openremap/core/cli/commands/diff_maps.py::diff_maps()`.
- Args: `stock`, `tuned` are `typer.Argument(exists, file_okay, readable,
  resolve_path)` — missing file exits **2**.  Key options: `--min-score/-s`
  (default **0.55**), `--threshold/-t` (default 0.0), `--top/-n` (50),
  `--compact`, `--json`, `--verbose/-v`, `--export DIR`, `--region/-r RANGE`,
  `--whole-file`, `--max-series-tables` (16), `--recipe F`, `--annotate F`.

## Flow (top → bottom)

1. **`_read_bin(path, label)`** — extension gate `VALID_EXTENSIONS`
   (`.bin/.ori/.hex/.s19/.srec/.mot`; wrong suffix → exit **1**) →
   `cli/io.py::load_binary_file` (read → empty check →
   `core/services/convert.py::decode_image`; error → exit **1**).
2. **`_parse_region(region)`** (imported from `cli/commands/scan_maps.py`) —
   `0xSTART-0xEND` hex slice; bad format / end<start → exit **1**.
3. **`find_changed_blocks(stock_data, tuned_data, 16)`** — Rust
   `openremap._rust` (`_rs/src/recipes/diff.rs`) — diff blocks merged when
   ≤16 bytes apart → `(offset, size, ob, mb)` tuples.
4. **`_scan_one(data, region_slice, min_score, max_series_tables,
   layout_default=not whole_file)`** (from `scan_maps.py`) per file:
   `services/maps/map_hunter.py::scan_map_axes(data, region)` (Rust) →
   `scan_map_tables(data, region, axes, min_score, max_series_tables)` (Rust)
   → optional calibration-region filter via `maps/layout.py::segment`
   (`tables_hidden` count).  Result dict: `{axes, tables, …}` with
   `MapTable` namedtuples.
5. **`_build_stock_index(stock_data, tables)`** — fingerprint =
   `(x_vals, y_vals)` read via `_read_axis_values(x/y_axis_offset)`; dict
   fingerprint → list of tables (handles shared-axis collisions).
6. **Pass 1 — exact match**: per tuned table, exact fingerprint → closest
   unused stock table by offset (one-to-one via `used_stock_offsets`).  Strided
   (compound) tables trust scanner offsets; contiguous tables use
   `_best_alignment` (±4-byte pad variants around both guessed offsets, fewest
   changed cells) → `_diff_cells` (max/avg abs + pct, changed count) →
   `_pearson` → `suspicious` when >90% cells changed and `r < 0.7`
   (`_SUSPICIOUS_CORR`).
7. **Pass 2 — near-match** (`_near_match_pass`): same-shape stock tables
   (pre-indexed by `(cols, rows, cell_width, byte_order, stride)`) with axes
   within `_NEAR_MATCH_AXIS_DEV_RATIO` (0.15, normalised by larger axis max)
   and cells `r >= _NEAR_MATCH_CELL_CORR` (0.95) → flagged
   `near_match`/`axis_changed`.
8. **Promotion** (`_promote_uncovered_changed_blocks` → `_repeated_row_table`):
   changed blocks no matched table covers that repeat identical rows
   (flat-Y/axis-less tables) become synthetic matches (`promoted`, cell_width
   forced 2); remaining uncovered spans → `_unidentified_changed_blocks`
   (per-byte coverage subtraction, ±4 `_PAD_SLACK`).
9. **Sort + filter**: sort by `max_abs` desc (inf last); `shown =
   above_threshold[:top]`, where `above_threshold` keeps `max_abs >= threshold`
   (inf always kept).
10. **`--recipe`**: `_load_recipe(recipe)` + `_annotate_matches` — byte-range
    overlap of recipe instructions on the stock side → per-match
    `recipe_instr_hits/recipe_cells_covered/untracked_cells` + aggregate
    counters.  **`--annotate`**: `recipes/recipe_maps.py::attach_maps(
    recipe_data, stock_data)` → writes the recipe augmented with a schema-4.4
    `maps[]` layer to the given path.
11. **Render** — JSON (strip `_stock_table/_tuned_table/_fp`; `_json_safe`
    turns ±inf into `"inf"`/`"-inf"`; groups by fingerprint, letter ids
    `A..Z,AA..`), or human: header + per-group rows with markers `↺ axes
    changed`, `⚑ no-axis`, `⚠ suspicious`, `↻ realigned`, `◆ recipe n/m`;
    `--export` → `_export_markdown` to `DIR/diff.md`; `--verbose` →
    `_print_map_grids` (stock + diff grids, changed cells yellow).

## Expected output

**Human**:

```
  OpenRemap — Map-Level Diff
  stock.bin  vs  tuned.bin  •  12 matched  •  1 only-in-stock  •  2 only-in-tuned  •  0 unidentified  •  scan 3.2s

  Group A — 3 map(s) · 16×16 u16 LE · X=[680, 685, 810, 925…+12]  Y=[4000…]
   Offset        Dim    Cells  Max Δ  Avg Δ   Max %   Avg %  Changed
  0x0012AB40  16×16  u16 LE   +14.0   +2.1   +3.2%  +0.5%  12/256   ⚠ suspicious
```

**JSON** — `stock, tuned, recipe, stock_size, tuned_size, stock_tables,
tuned_tables, stock_tables_hidden, tuned_tables_hidden, matched_count,
above_threshold, only_in_stock_count, only_in_tuned_count,
unidentified_changed_count, unidentified_changed[], scan_seconds, groups[]
(id/count/cols/rows/cell_width/byte_order/x_axis/y_axis), matches[],
only_in_stock[], only_in_tuned[]`; each match: `offset_stock, offset_tuned,
cols, rows, cell_width, byte_order, stride, offset_delta, realigned,
suspicious, correlation, near_match, promoted, max_abs, avg_abs, max_pct,
avg_pct, changed_cells, total_cells, group` (+ `recipe_instr_hits,
recipe_cells_covered, untracked_cells` with `--recipe`).

**Exit codes:** `0` always on completion — including zero matches or an
unrelated-pair warning · `1` read/decode/extension or `--region` parse error ·
`2` missing file arg.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 bin-reading CLI commands (`identify`, `analyze`, `audit`, `layout`, `merge`, `cook`, `tune`, `validate`, `checksum`, `health`, `scan-vins`, `scan-maps`, `diff-maps`, `routine`) + TUI (via `decode_image`) |
| `_scan_one` / `_parse_region` | `cli/commands/scan_maps.py` | `scan-maps` single-file + batch modes, `diff-maps` |
| `scan_map_axes` / `scan_map_tables` | `services/maps/map_hunter.py` | `scan-maps`, `cook`, `cook-volatile`, `health`, `analyze`, `recipe_maps`, `recipe_regions`, `server` (deprecated) |
| `MapTable` | `services/maps/map_hunter.py` | `scan-maps`, `recipe_maps`, `layout`, `map_classifier`, `xrefs`, `map_exporter`, `analyze` |
| `find_changed_blocks` (Rust) | `_rs/src/recipes/diff.rs` | `recipe_builder.py::ECUDiffAnalyzer.find_changes` (→ `cook`, `cook-volatile`, audit, TUI), `diff-maps` |
| `attach_maps` | `recipes/recipe_maps.py` | `cook`, `cook-volatile`, TUI tune flow, `merge` (via `merge_recipes`), `diff-maps --annotate` |

## Gotchas

- **`--threshold` filters *display*, not matching** — rows with `max_abs <
  threshold` are dropped from the shown list (inf always passes); JSON
  `matched_count` still counts all matches, `above_threshold` the filtered
  count.
- **Changed-axis maps**: a tuner editing breakpoints breaks the exact
  fingerprint match — the near-match pass (0.15 axis-deviation ratio, 0.95
  cell correlation) is what rescues them into `↺ axis_changed` rows; without
  it they'd silently land in only-in-*.
- `suspicious` (`>90%` cells changed AND `r < 0.7`) usually means two
  *different* maps sharing axes got aligned, not a heavy retune; near-matches
  can never be suspicious by construction (they require `r >= 0.95`).
- `_best_alignment` realignment (±4 bytes) can shift a match off the scanner's
  offsets — flagged `↻ realigned`; strided (compound) table halves trust the
  Rust-split offsets instead.
- Promoted tables are *synthetic* (from changed bytes, not the axis scanner) —
  `cell_width` is forced 2, `correlation` is None, and their `_fp` is empty;
  in JSON they carry `promoted: true`.
- JSON percentages serialize ±inf as the strings `"inf"`/`"-inf"` (`_json_safe`)
  — beware when feeding downstream numeric parsers.
- `--whole-file` disables the calibration-region layout filter
  (`layout_default = not whole_file`); hidden tables surface via
  `*_tables_hidden`.
- When `match_pct < 5%` of the smaller file's tables match, the command prints
  a yellow "files may not be from the same ECU" warning — still exit 0.
