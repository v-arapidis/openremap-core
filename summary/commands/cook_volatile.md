# `cook_volatile` — command summary (fast-lookup)

> One-file reference for
> `openremap cook-volatile <original> <modified> [-o out] [--no-exclude]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.  The canonical background is `notes/recipes/cook-volatile.md` —
> read it before any cook/recipe-portability work.

## Entry & registration

- Command: `openremap cook-volatile <ORIGINAL> <MODIFIED>
  [--output/-o PATH] [--context-size/-c N] [--allow-non-unique]
  [--no-exclude] [--exclude-uncertain] [--accept-volatile]
  [--annotate-maps/--no-annotate-maps] [--pretty/--compact]`
- Registered in `openremap/core/cli/main.py` (import line 39,
  `app.command(name="cook-volatile")` block, right after `cook`) →
  `openremap/core/cli/commands/cook_volatile.py::cook_volatile()`.
- Both file args: `exists, file_okay, dir_okay=False, readable,
  resolve_path` (missing file exits **2**).  `--output` is `writable`.
- Sibling of `cook` — identical diff pipeline plus a volatile-classification
  pass; `cook` itself is untouched (no new flags there).

## Flow (top → bottom)

1. **Read + decode both files** — `cook.py::_read_bin(path, label)`:
   `_check_bin` (extension gate `.bin/.ori/.hex/.s19/.srec/.mot` →
   exit **1**) then `cli/io.py::load_binary_file` (read/decode/empty →
   exit **1**).
2. **Cook** — `core/services/recipes/recipe_builder.py::ECUDiffAnalyzer(
   original_data, modified_data, original_filename, modified_filename,
   context_size, require_unique=not allow_non_unique)` →
   `analyzer.build_recipe()`: Guard 1 size match (hard error), Guard 2
   identity match (`identify_ecu` both sides → warning), `find_changes()`
   → Rust `openremap._rust.find_changed_blocks` (16-byte merge threshold)
   + per-change `_get_verified_context` → `core/services/entropy.py::
   find_unique_context` (geometric 32→512-byte expansion, entropy +
   whole-binary uniqueness), Guard 3 non-unique-anchor abort (unless
   `--allow-non-unique` → warnings), annotator flags.  Recipe schema 4.3.
3. **Classify** — `core/services/recipes/volatile.py::classify_volatile(
   recipe, original_data, exclude_uncertain=exclude_uncertain)` →
   `VolatileReport{excluded, flagged}`.  Internally:
   `collect_checksum_stores(original_data)` (runs the verified family
   detectors `detect_me7`, `detect_me7_multipoint`, `detect_me7_rolling`,
   `detect_me7_multirange`, `detect_ms43`, `detect_denso`,
   `detect_ironfelix` → store byte ranges), `scan_vins(original_data,
   min_confidence=0.9)` filtered to `wmi_known`, and
   `maps/layout.py::find_ident_blocks`.  Strong (VIN / CHECKSUM_STORE) →
   excluded; weak (SERIAL_OR_IDENT / COUNTER_OR_SERIAL) → flagged
   (promoted with `--exclude-uncertain`).  Pure function — the recipe is
   NOT modified here.
4. **Filter** — unless `--no-exclude`: drop `report.excluded` indices from
   `recipe["instructions"]`.  Recompute `_recompute_stats` (cook_volatile
   .py:63 — kept set only), `recipe_builder.py::compute_fingerprint`
   (kept set), `metadata.instruction_count`.
5. **Volatile section (schema 4.5)** — `recipe["volatile"] =
   {excluded[], flagged[], summary{excluded_count, flagged_count,
   bytes_excluded}}` via `VolatileFinding.to_dict()` (includes
   `index` = pre-exclusion index).  With `--no-exclude`: nothing removed,
   so all findings are restamped `action="flagged"` (recipe
   self-consistency).  `metadata.source="cook_volatile"`,
   `metadata.volatile=summary`, `metadata.excluded_volatile=not no_exclude`.
6. **Shared map scan** — `maps/map_hunter.py::scan_map_tables(
   original_data, min_score=0.55, max_series_tables=16)` — one scan
   shared by annotation and region tags (never scan twice).
7. **Map annotation (optional, default on)** — `recipes/recipe_maps.py::
   attach_maps(recipe, original_data, tables=shared_tables)` runs AFTER
   filtering so `maps[].instruction_refs` index the KEPT set.  It bumps
   schema to 4.4 — `recipe["schema_version"] = "4.5"` is set AFTER it so
   **4.5 always wins** (ordering rule).
8. **Region tags (advisory)** — `cook.py::_tag_regions` →
   `recipes/recipe_regions.py::tag_instruction_regions` (CODE_AREA
   flags; never blocks/filters — any failure degrades to an empty
   summary).
9. **Same-file stamp** — when `--allow-non-unique` and
   `analyzer.cook_warnings()` mention non-unique: `metadata.portability =
   "same_file_only"` (tune/validate enforce via `ecu.sha256` unless
   `--force`).
10. **Errors** — any exception: styled error + `typer.Exit(1)`; the
    "non-unique context" message suggests re-running with
    `--allow-non-unique`.
11. **Review output** — unless `--accept-volatile`:
    `_print_review_lines(excluded, "N instruction(s) excluded as
    volatile:")` and `(flagged, "…flagged for review:")` (stderr);
    `_print_volatile_summary` (always; the `--no-exclude` ⚠ warning goes
    to stderr); `cook.py::_print_region_warning`.
12. **Emit** — `json.dumps(recipe, indent=2 if pretty else None,
    sort_keys=True)`.  `--output`: `mkdir(parents=True)` + write_text,
    OSError → exit **1**; else stdout.  Then `cook.py::_print_summary`
    (ECU, match key, schema, counts, flagged count, saved path).

## Expected output

**Human** — the recipe JSON (stdout or `--output`) plus review lines:

```
  Volatile summary: 2 excluded (8 bytes), 1 flagged

  2 instruction(s) excluded as volatile:
     0xE18F4 — VIN (0.95): overlaps VIN-structured record 'WAUZZZ8V...' at ...
     0x1FFFE0 — CHECKSUM_STORE (0.95): overlaps ME7 main checksum store at ...

  ✅ Recipe built successfully
  ECU                    Bosch · EDC17
  Match Key              EDC17C66::...
  Format Version         4.5
  Instructions           77
  Bytes Changed          7,820
```

**JSON** — the recipe itself (schema **4.5**): standard 4.3 fields
(`ecu`, `statistics`, `instructions` with `offset/ob/mb/ctx/context_after/
ctx_entropy/ctx_unique/ctx_expanded/flags`, `fingerprint`) + `maps[]`
(when `--annotate-maps`, refs index kept set) + `volatile{excluded[],
flagged[], summary}`.  Deterministic except `creator.created_at`.

**Exit codes:** `0` ok · `1` extension/read/decode/build/write error ·
`2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `_read_bin`, `_check_bin`, `_print_summary`, `_print_region_warning`, `_tag_regions` | `cli/commands/cook.py` | `cook` (its home command; cook-volatile imports them) |
| `load_binary_file` | `cli/io.py` | every single-file bin-reading command + TUI (via `decode_image`) |
| `ECUDiffAnalyzer` / `build_recipe` | `core/services/recipes/recipe_builder.py` | `cook`, TUI cook flow (`tui/app.py`), volatile-aware `audit` (re-cook), `server` (deprecated) |
| `compute_fingerprint` | `core/services/recipes/recipe_builder.py` | `recipe_builder` itself, `audit`, `recipe_merge` |
| `classify_volatile` | `core/services/recipes/volatile.py` | `audit` (re-verify of declared exclusions) |
| `collect_checksum_stores` | `core/services/recipes/volatile.py` | `classify_volatile` (this command + audit path) |
| `scan_map_tables` | `core/services/maps/map_hunter.py` | `cook`, `scan-maps`, `health`, `analyze`, `layout.segment`, `attach_maps`, `recipe_regions`, `server` (deprecated) |
| `attach_maps` | `core/services/recipes/recipe_maps.py` | `cook`, `diff-maps`, `recipe_merge`, TUI |
| `tag_instruction_regions` | `core/services/recipes/recipe_regions.py` | `cook` (+ cook-volatile via `_tag_regions`) |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `identify`, `scan-vins`, `health`, `analyze`, cook annotator |
| `find_ident_blocks` | `core/services/maps/layout.py` | `layout`, `vin_scanner`, `confidence`, `analyze`, `audit` |
| checksum detectors (`detect_me7`…`detect_ironfelix`) | `core/services/checksums/*.py` | `checksum` command, `health`, `analyze` (see checksum.md table) |

## Gotchas

- **Ordering rule (critical):** `attach_maps` writes `instruction_refs`
  as indices — run it AFTER filtering; and it bumps schema to 4.4, so the
  4.5 level is set AFTER it or 4.4 wins.
- **`--no-exclude` self-consistency:** nothing is removed, so excluded
  findings are restamped `action="flagged"` — the recipe never claims a
  removal that did not happen.  The recipe is then only reliable on THIS
  exact binary.
- **Stats/fingerprint are recomputed over the KEPT set** after filtering
  (and `metadata.instruction_count`) — stale full-diff numbers would
  break the volatile-aware audit.
- **Thresholds (corpus-justified):** VIN exclusion requires a known WMI
  *and* conf ≥ 0.9; `CHECKSUM_STORE` requires a verified family detector
  firing on this binary — the closed-config sweep is NEVER an exclusion
  source (single-store whole-file matches are weak evidence).
- **Safety fallback:** when no detector fires, no store exclusions are
  made and the recipe keeps today's safe behavior — it fails on another
  car instead of guessing (the right question is volatility, and
  checksum-ness is one sufficient cause).
- **Portability proof:** the e2e test injects a real VIN pair into the
  real EDC17 stock — `cook-volatile` output applies cleanly to a
  different-VIN stockB via `tune` (exit 0); plain `cook` hard-fails
  (exit 1).
- `--allow-non-unique` stamps `metadata.portability="same_file_only"` —
  enforced by tune/validate via `ecu.sha256` unless `--force`.
- `VolatileFinding.index` is the PRE-exclusion instruction index (review
  cross-reference); `maps[].instruction_refs` are POST-exclusion.
