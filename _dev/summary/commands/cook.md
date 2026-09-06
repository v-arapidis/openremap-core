# `cook` — command summary (fast-lookup)

> One-file reference for `openremap cook <original> <modified> [-o
> recipe.remap] [--context-size N] [--allow-non-unique] [--pretty]
> [--no-annotate-maps]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap cook <ORIGINAL> <MODIFIED> [--output recipe.remap]
  [--context-size N] [--allow-non-unique] [--pretty] [--annotate-maps/--no-annotate-maps]`
- Registered in `openremap/cli/main.py` via `app.command(name="cook")`
  → `openremap/cli/commands/cook.py::cook()`.
- `cook_volatile` shares this command's `_read_bin` + tag/annotate wiring.

## Flow (top → bottom)

1. **Read + decode both inputs** — `_read_bin` (extension gate + `cli/io.py
   ::load_binary_file` → `core/services/convert.py::decode_image`).
2. **Pre-cook guards** — size mismatch (hard error) + identity match guard
   (both binaries must identify as the same family) inside
   `ECUDiffAnalyzer`.
3. **Diff → recipe** — `core/services/recipes/recipe_builder.py
   ::ECUDiffAnalyzer(original_data, modified_data, context_size,
   require_unique=not allow_non_unique).build_recipe()`:
   - `openremap._rust::find_changed_blocks` (Rust byte diff, 16-byte merge)
   - per change: `core/services/entropy.py::find_unique_context` (geometric
     32→512-byte expansion, Shannon entropy + whole-binary uniqueness)
   - `core/services/recipes/annotator.py::RecipeAnnotator` (non-destructive
     flags)
   - **Guard 3:** non-unique context anchors abort unless `--allow-non-unique`
     (stamps `metadata.portability = "same_file_only"`).
4. **One shared stock scan** — `core/services/maps/map_hunter.py
   ::scan_map_tables(original_data, min_score=0.55, max_series_tables=16)` —
   used by BOTH annotation and region tags (never scanned twice).
5. **Annotation** — if `--annotate-maps` (default):
   `core/services/recipes/recipe_maps.py::attach_maps(recipe, original_data,
   tables=shared_tables)` → schema **4.4** `maps[]`.  `--no-annotate-maps` →
   lean **4.3**.
6. **Region tags (advisory)** — `core/services/recipes/recipe_regions.py
   ::tag_instruction_regions(recipe, original_data, tables=shared_tables)` —
   `region` field + `CODE_AREA` flag; never blocks/filters.
7. **Write** — stdlib `json.dumps` (byte-stable output contract), `-o`
   path or stdout.

## Expected output

**Recipe file** (JSON) — schema 4.4 default / 4.3 lean: `type,
schema_version, source, metadata, ecu, statistics, instructions[]`,
plus `maps[]` (4.4) and per-instruction `region` tags.  Each instruction:
`offset, offset_hex, size, ob, mb, ctx, context_after, context_size,
ctx_entropy, ctx_unique, ctx_expanded, description, flags, region`.

**Human** — identity summary, instruction count, bytes changed, flagged
count, saved path.

**Exit codes:** `0` success · `1` bad input (extension/empty/read), Guard-3
abort, size mismatch, or identity mismatch · `2` missing file (Click).

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 13 bin-reading CLI commands, `analyze`, TUI |
| `ECUDiffAnalyzer` / `build_recipe` | `core/services/recipes/recipe_builder.py` | `cook-volatile`, TUI cook |
| `find_changed_blocks` (Rust) | `openremap._rust` | `recipe_builder` only, plus `diff-maps` CLI |
| `find_unique_context` | `core/services/entropy.py` | `recipe_builder` only |
| `RecipeAnnotator` | `core/services/recipes/annotator.py` | `recipe_builder` only |
| `attach_maps` | `core/services/recipes/recipe_maps.py` | `cook`, `cook-volatile` |
| `tag_instruction_regions` | `core/services/recipes/recipe_regions.py` | `cook`, `cook-volatile` |
| `scan_map_tables` | `core/services/maps/map_hunter.py` | `scan-maps`, `diff-maps`, `analyze`, `health` |
| `identify_ecu` (identity guard) | `core/services/identify/identifier.py` | `identify`, `analyze`, `health`, `scan_maps`, tune/validate (`preflight`), TUI |

## Gotchas

- **Byte-stable output is a contract** — serialisation stays stdlib `json`
  (do not switch to orjson for `dumps`; recipe files must be reproducible).
- **One stock scan only** — `shared_tables` feeds both `attach_maps` and
  `tag_instruction_regions`; never scan the stock twice.
- Guard 3 strictness is the portability promise — `--allow-non-unique` is
  the documented opt-out and stamps `same_file_only`.
- `cook_volatile` reuses `cook._read_bin` — changing it affects both.
