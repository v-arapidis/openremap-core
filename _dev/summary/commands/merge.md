# `merge` — command summary (fast-lookup)

> One-file reference for `openremap merge <a.remap> <b.remap> [--stock
> original.bin] [-o out] [--strict]`: entry point, exact call flow, expected
> output, and every shared function it touches (so a change to any of them can
> be checked against all consumers).  Keep this file updated when the command
> or its dependencies change.

## Entry & registration

- Command: `openremap merge <RECIPE_A> <RECIPE_B> [--stock BIN] [-o OUT]
  [--strict]` — git-like three-way merge of two recipes against a common stock
  binary.
- Registered in `openremap/core/cli/main.py` via
  `app.command(name="merge")(merge)` → CLI function
  `openremap/core/cli/commands/merge.py::merge()`.
- Args: `recipe_a`, `recipe_b` are `typer.Argument(exists, file_okay, readable,
  resolve_path)` — missing file exits **2**.  Options: `--stock` (exists),
  `--output/-o` (`writable`), `--strict` (abort instead of skip).

## Flow (top → bottom)

1. **`_load_recipe(path, label)`** ×2 — `orjson.loads(path.read_text())`;
   `OSError`/`JSONDecodeError` or a dict without `instructions` → styled
   error + exit **1**.
2. **`--stock`** → `cli/io.py::load_binary_file(stock, "Stock")` (read →
   empty check → `core/services/convert.py::decode_image`; error → exit **1**).
3. **`services/recipes/recipe_merge.py::merge_recipes(data_a, data_b,
   name_a=…, name_b=…, stock_data=…, strict=…)`**:
   - `recipe_builder.py::check_schema_version` for both recipes.
   - **volatile drop**: any input carrying a `volatile` dict (schema 4.5) gets
     a warning — the exclusion evidence does not transfer to a merged list.
   - **ECU gate**: differing `ecu.match_key` → `MergeConflict`.
   - **No `--stock`**: both recipes must declare **identical** `ecu.sha256`
     (and have `match_key`) → else `MergeConflict` (this is the
     stock-required behaviour).
   - **With `--stock`**: `preflight.py::check_file_size` per recipe → warnings;
     inner `validated()` re-checks every instruction's `ob` at its exact
     offset against the stock; mismatched/out-of-range instructions are
     **skipped** with a warning, or abort the merge when `--strict`.
   - **Combine** (`add()`): identical `(size, ob, mb)` at the same offset
     dedupes; same offset with different values → `MergeConflict`; overlapping
     byte spans at different offsets → `MergeConflict`.
   - **Guard 3 re-check**: `services/entropy.py::count_unique_in_window(
     stock_data, bytes.fromhex(ctx)+bytes.fromhex(ob), 0, len(stock_data))`
     for every merged instruction; non-unique anchors → warning, or abort with
     `--strict`.
   - **Build merged recipe** (schema 4.3): `creator` from
     `recipe_builder.py::build_creator_block()`, `fingerprint` from
     `compute_fingerprint(instructions)`, `statistics` via `_compute_stats`,
     `metadata.merged_from/merged_fingerprints`, `ecu.cook_warnings = warnings`.
   - **With `--stock`**: `recipes/recipe_maps.py::attach_maps(merged_recipe,
     stock_data)` best-effort (exception swallowed) — bumps the output to
     schema **4.4**.
4. **`MergeConflict`** → styled `✗ Merge conflict:` message + exit **1**.
5. **Render** — `✅ Merged a (N) + b (M) → K instructions.` + yellow warnings
   (stderr); then the merged recipe as `json.dumps(..., indent=2, sort_keys)`
   to stdout, or written to `-o` (mkdir parents; write error → exit **1**)
   with a `Saved merged recipe to … (schema X)` note.

## Expected output

**Human** (with `-o`):

```
  ✅ Merged egr_off.remap (3 instructions) + stage1.remap (5 instructions) → 6 instructions.
  ⚠  b.remap: 2 instruction(s) skipped — they do not match the stock binary (…)

  Saved merged recipe to both.remap (schema 4.4)
```

**JSON** (stdout without `-o`, or file content) — the merged recipe dict:
`schema_version` (4.3/4.4), `type: "recipe"`, `source: "recipe_merge"`,
`creator`, `fingerprint`, `metadata{name, merged_from, merged_fingerprints,
…}`, `ecu{…, cook_warnings[]}`, `statistics{total_changes, total_bytes_changed,
…}`, `instructions[]` (sorted by offset).

**Exit codes:** `0` success (skip-warnings included) · `1` `MergeConflict`
(ECU mismatch, no-stock sha256 mismatch, same-offset/overlapping conflicts,
`--strict` failures), read/parse/write errors · `2` missing file arg.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 bin-reading CLI commands (`identify`, `analyze`, `audit`, `layout`, `merge`, `cook`, `tune`, `validate`, `checksum`, `health`, `scan-vins`, `scan-maps`, `diff-maps`, `routine`) + TUI (via `decode_image`) |
| `merge_recipes` / `MergeConflict` | `recipes/recipe_merge.py` | CLI `merge` command only |
| `attach_maps` | `recipes/recipe_maps.py` | `cook`, `cook-volatile`, TUI tune flow, `diff-maps --annotate`, merge (via `merge_recipes`) |
| `check_file_size` | `recipes/preflight.py` | the three validators, `patcher.preflight_warnings`, `tune`, `validate` |
| `count_unique_in_window` | `services/entropy.py` | `find_unique_context` anchor-uniqueness in `entropy.py` (cook), merge Guard 3 |
| `build_creator_block` / `compute_fingerprint` | `recipes/recipe_builder.py` | `cook`, `cook-volatile`, `audit`, merge |
| `check_schema_version` | `recipes/recipe_builder.py` | validators, `tune`, `cook`, `cook-volatile`, `audit`, merge |

## Gotchas

- **Stock-required**: without `--stock` both recipes must share a byte-identical
  original (`ecu.sha256`) — otherwise the merge refuses.  With `--stock`,
  instructions that don't match the stock's `ob` are *skipped with a warning*;
  only `--strict` turns that into an abort.
- Conflict rules are positional: identical `(size, ob, mb)` dedupes; same
  address with different edits conflicts; overlapping ranges at different
  offsets conflict — "merge by hand" is the only path out.
- **Volatile sections (schema 4.5) are dropped**, with a warning pointing at
  `cook-volatile` — exclusion evidence cannot survive the combination.
- Guard 3 re-checks ctx+ob anchor uniqueness in the stock; non-unique anchors
  survive as warnings (portability risk) or abort under `--strict`.
- Output schema depends on input: 4.4 (with `maps[]` re-annotated from the
  stock) when `--stock` is given, else 4.3; `attach_maps` failures are
  swallowed so a merge never fails on annotation.
