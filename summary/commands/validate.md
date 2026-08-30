# `validate` — command summary (fast-lookup)

> One-file reference for `openremap validate before|check|after <bin> <recipe>
> [--json] [-o out]`: entry point, exact call flow, expected output, and every
> shared function it touches (so a change to any of them can be checked against
> all consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap validate <SUBCOMMAND> <FILE> <RECIPE> [--json] [--output PATH]`
  with subcommands `before` (pre-tune strict check), `check` (whole-binary
  ob-existence scan), `after` (post-tune mb confirmation).
- Registered in `openremap/core/cli/main.py` via `app.add_typer(validate.app,
  name="validate")` → Typer app in `openremap/core/cli/commands/validate.py`
  (three primary commands `before` / `check` / `after`; three **hidden**
  deprecated aliases `strict` / `exists` / `tuned`).
- CLI function paths: `validate.py::before()`, `::check()`, `::after()`
  (thin wrappers over `_run_before/_run_check/_run_after`).
- Args: `FILE` and `RECIPE` are `typer.Argument(exists, file_okay, readable,
  resolve_path)` — Click enforces existence → missing file exits **2**.  Both
  support `--json` and `--output/-o`.

## Flow (top → bottom)

1. **`_read_bin(path, label)`** — extension gate `_ALLOWED_BIN`
   (`.bin/.ori/.hex/.s19/.srec/.mot`; wrong suffix → styled error + exit **1**)
   → `cli/io.py::load_binary_file(path, label)` (read → empty check →
   `core/services/convert.py::decode_image(raw)`; read/decode/empty errors →
   styled stderr + exit **1**).
2. **`_read_recipe(path)`** — extension gate (`.remap/.openremap/.json`; else
   exit **1**) → `path.read_text` → `orjson.loads` (parse error → exit **1**).
3. **`before`** (`_run_before`): construct `ECUStrictValidator(target_data,
   recipe, target_name, recipe_name)` — ctor runs
   `recipes/recipe_builder.py::check_schema_version(recipe)`; then
   `validator.check_file_size()` → `recipes/preflight.py::check_file_size(recipe,
   len(data))`; `validator.check_match_key()` →
   `preflight.py::check_match_key(recipe, data, filename)` →
   `identify/identifier.py::identify_ecu` (both return `None`/warning-only, never
   fatal); `validator.validate_all()` → `preflight.py::scan_exact_matches(data,
   instructions, "ob")` — exact-offset read/compare, kinds
   `ok/malformed/bounds/mismatch`; `validator.to_dict()` → report
   (`target_file, target_md5, summary, failures, all_results`).
4. **Same-file-only gate (ISSUE-2)** — `preflight.py::check_same_file_only(
   recipe_dict, target_data)`; a recipe stamped
   `metadata.portability == "same_file_only"` whose target sha256 ≠
   `ecu.sha256` forces `report["summary"]["safe_to_patch"] = False`.
5. **`check`** (`_run_check`): `ECUExistenceValidator` (ctor
   `check_schema_version`) → `check_file_size`/`check_match_key` →
   `validate_all()`: per instruction `services/entropy.py::find_all_offsets(
   data, pattern)` → classify `MatchStatus.EXACT/SHIFTED/MISSING/INVALID`;
   `verdict()` → `safe_exact | shifted_recoverable | missing_unrecoverable |
   invalid_recipe`; `to_dict()`.
6. **`after`** (`_run_after`): `ECUPatchedValidator(patched_data, recipe, …)`
   → `verify_all()` → `scan_exact_matches(data, instructions, "mb",
   compare_field="ob")` (kind `stale` = ob still present) → `to_dict()`
   (`patched_file, patched_md5, summary, failures, all_results`).
7. **Render** — `_write_json` (orjson-free stdlib `json.dumps` indent=2; file
   with mkdir or stdout; write error → exit **1**) or human layout via
   `_warn_line` (⚠ size / match-key).  Human lists first 10 failures only
   (`before`), or all shifted/missing/invalid details (`check`, `after`).

## Expected output

**Human (`before`, pass)**:

```
  Validating target.bin against recipe.remap …

  ✅ Safe to tune
  Target                target.bin
  MD5                   <hex>
  Instructions          12
  Passed                12
```

**Human (`check`, shifted)** — `Verdict: SHIFTED RECOVERABLE` + Exact/Shifted/
Missing/Invalid counts, per-instruction `expected 0x… → found at shift ±N`.

**Human (`after`, fail)** — `❌ Tune NOT confirmed…` + per-instruction
`offset 0x… size N bytes — reason`.

**JSON** (`before`): `target_file, recipe_file, target_md5`,
`summary{total, passed, failed, score_pct, safe_to_patch}`, `failures[]`,
`all_results[]` + `same_file_only{stamped, allowed, note}`.  (`check`:
`summary{total, exact, shifted, missing, invalid, *_pct, verdict}` + `results[]`.
`after`: `patched_file, patched_md5`, `summary{…, patch_confirmed}`,
`failures[]`, `all_results[]`.)

**Exit codes:** `0` ok · `1` read/recipe/validator error, any `before` failure,
`check` verdict `missing_unrecoverable|invalid_recipe`, `after` not confirmed ·
`2` missing file arg.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 bin-reading CLI commands (`identify`, `analyze`, `audit`, `layout`, `merge`, `cook`, `tune`, `validate`, `checksum`, `health`, `scan-vins`, `scan-maps`, `diff-maps`, `routine`) + TUI (via `decode_image`) |
| `ECUStrictValidator` | `recipes/validate_strict.py` | `tune` Phase 1, `patcher.py` (internal pre-write), TUI tune flow |
| `ECUExistenceValidator` | `recipes/validate_exists.py` | TUI tune flow, `server` (deprecated) |
| `ECUPatchedValidator` | `recipes/validate_patched.py` | `tune` Phase 3, TUI tune flow |
| `check_same_file_only` | `recipes/preflight.py` | `tune` (with `force`); recipes it gates are stamped by `cook --allow-non-unique` |
| `check_file_size` / `check_match_key` | `recipes/preflight.py` | all three validators, `patcher.preflight_warnings`, `tune`, `recipe_merge` (size only), `server` (deprecated) |
| `scan_exact_matches` | `recipes/preflight.py` | `validate_strict` (field `ob`), `validate_patched` (field `mb`, `compare_field="ob"`) |
| `find_all_offsets` | `services/entropy.py` | `validate_exists` only (sole consumer) |
| `check_schema_version` | `recipes/recipe_builder.py` | validators, `tune`, `cook`, `cook-volatile`, `audit`, `merge` |

## Gotchas

- **Strict = exact-offset, all-or-nothing**: `before` reads `ob` at the exact
  recorded offset for *every* instruction; any mismatch → `safe_to_patch=False`
  and exit **1** (no partial tolerance).
- Size/match-key mismatches are **warnings only** — they never flip the exit
  code or `safe_to_patch`; the same-file-only gate is the only check that can
  force `safe_to_patch=False`.
- `check` semantics: invalid-hex wins, then missing, then shifted.  A shifted
  verdict exits **0** — `tune` may still recover via its ±2 KB ctx+ob anchor
  search; only `missing_unrecoverable`/`invalid_recipe` exit 1 (wrong ECU).
- `after` reports `stale` when `ob` is still present (patch never applied) and
  `mismatch` for a third value; `patch_confirmed` is true only when all pass.
- Deprecated aliases `strict`/`exists`/`tuned` stay registered (hidden from
  `--help`), print a rename note, then delegate to the same `_run_*` cores.
- Recipes are parsed with **orjson** (JSON only, no comments); recipe suffix
  `.json`/`.openremap` accepted alongside `.remap`.
