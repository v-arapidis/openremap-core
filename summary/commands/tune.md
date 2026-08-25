# `tune` — command summary (fast-lookup)

> One-file reference for `openremap tune <target> <recipe> [-o out]
> [--skip-validation] [--force] [--json] [--report path]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap tune <TARGET> <RECIPE> [--output PATH]
  [--skip-validation] [--force] [--json] [--report PATH]`
- Registered in `openremap/cli/main.py` via `app.command(name="tune")`
  → `openremap/cli/commands/tune.py::tune()`.
- The original file is never modified; the tuned result is written to a
  separate output (default `<target_stem>_tuned<ext>`).

## Flow (top → bottom)

1. **Read inputs** — `_read_bin(target, "Target")` (extension gate +
   `cli/io.py::load_binary_file` → `core/services/convert.py::decode_image`);
   `_read_recipe(recipe)` → `orjson.loads`.
2. **Portability gate** — `core/services/recipes/preflight.py
   ::check_same_file_only(recipe_dict, target_data, force)` — refuses a
   `same_file_only` recipe on a different binary unless `--force` (loud
   warning; mechanical phases still run and can abort).
3. **Phase 1 — validate before** (skip with `--skip-validation`) —
   `core/services/recipes/validate_strict.py::ECUStrictValidator` —
   every `ob` must sit at its exact offset in the target; the validator
   also runs `check_schema_version` (accepts ≥ 4.3) from
   `recipe_builder.py`.
4. **Phase 2 — apply** — `core/services/recipes/patcher.py::ECUPatcher
   (…).apply_all()` — anchor search `ctx + ob + context_after` within
   ±2048 bytes of the expected offset; the patcher re-runs strict
   validation + schema check internally before writing.
5. **Phase 3 — validate after** — `core/services/recipes/validate_patched.py
   ::ECUPatchedValidator` — confirm every `mb` landed in the output.
6. **Write only if all phases pass** — all-or-nothing: partial patches
   never happen.  Combined report (`--json` / `--report`).

## Expected output

**Tuned binary** at `--output` (byte-correct when exit 0), plus a
three-phase human report (✓/✗ per phase, md5s).  `--json` prints the
combined report dict; `--report path` saves it.

**Exit codes:** `0` success · `1` any phase failed, gate refused, or
read/decode error · `2` missing file (Click).

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 13 bin-reading CLI commands, `analyze`, TUI |
| `orjson.loads` (recipe read) | — (via `_read_recipe`) | `validate` (its own `_read_recipe`), `merge`, `audit`, `diff-maps`, TUI |
| `check_same_file_only` | `core/services/recipes/preflight.py` | `tune`, `validate` |
| `ECUStrictValidator` | `core/services/recipes/validate_strict.py` | `tune`, `validate`, TUI validate |
| `ECUPatcher` / `apply_all` | `core/services/recipes/patcher.py` | `tune`, TUI tune |
| `ECUPatchedValidator` | `core/services/recipes/validate_patched.py` | `tune`, `validate`, TUI validate |
| `check_schema_version` | `core/services/recipes/recipe_builder.py` | via validators/patcher: `validate_strict`/`validate_patched`/`validate_exists` (validate), `patcher` (tune), `recipe_merge` (merge), `audit` |

## Gotchas

- **All-or-nothing is the safety contract** — the output is written only
  when Phase 1 + 2 + 3 pass; `--skip-validation` bypasses 1 + 3 (escape
  hatch for scripted pipelines, never the default).
- The portability gate is machine-enforced: `same_file_only` recipes refuse
  any other binary without `--force` (and `--force` still runs the
  mechanical checks).
- Recipe *serialisation* stays stdlib json; the *parse* path uses orjson —
  keep that split (byte-stability rule).
- `validate` shares the same validators + preflight — changing their
  behaviour changes both commands.
