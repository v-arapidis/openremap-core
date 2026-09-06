# `audit` — command summary (fast-lookup)

> One-file reference for `openremap audit <stock> <tuned> <recipe> [--json]`:
> entry point, exact call flow, expected output, and every shared function it
> touches (so a change to any of them can be checked against all consumers).
> Keep this file updated when the command or its dependencies change.

## Entry & registration

- Command: `openremap audit <STOCK> <TUNED> <RECIPE> [--json]` — the "receipt
  check": do the three artifacts actually belong together?
- Registered in `openremap/core/cli/main.py` via
  `app.command(name="audit")(audit_cmd)` → CLI function
  `openremap/core/cli/commands/audit.py::audit_cmd()`.
- Args: `stock`, `tuned`, `recipe` are `typer.Argument(exists, file_okay,
  readable, resolve_path)` — missing file exits **2**.  Only `--json` flag
  (no `--output`).

## Flow (top → bottom)

1. **`_read_bin(path, label)`** — note: **no extension gate** here (unlike
   validate/diff-maps) — straight to
   `cli/io.py::load_binary_file(path, label)` (read → empty check →
   `core/services/convert.py::decode_image(raw)`; any error → exit **1**).
   Runs for both `stock` and `tuned`.
2. **Recipe load** — `orjson.loads(recipe.read_text(encoding="utf-8"))`;
   `OSError`/`JSONDecodeError` or a non-dict without `instructions` →
   styled error + exit **1**.
3. **`services/recipes/audit.py::audit(stock_data, tuned_data, recipe,
   stock_name=…, tuned_name=…, recipe_name=…)`** → `AuditResult`:
   - `check_schema_version(recipe)`; `ValueError` when the two binaries differ
     in size.
   - **Verdict 1 — Provenance**: `recipe["ecu"]["sha256"]` vs
     `hashlib.sha256(stock_data)` hexdigest.
   - **Verdict 2 — Fingerprint**: re-cook `ECUDiffAnalyzer(original_data=stock,
     modified_data=tuned, original_filename=…, modified_filename=…,
     require_unique=False).build_recipe()` and compare
     `recipe["fingerprint"]` (covers `(offset, ob, mb)` only — metadata and
     the `maps` layer excluded).  For schema-4.5 volatile recipes: subset check
     (kept ⊆ re-cooked diff via `_inst_key`), self-consistency via
     `recipe_builder.py::compute_fingerprint`, and re-verify of excluded
     offsets via `recipes/volatile.py::classify_volatile(recomputed_recipe,
     stock_data, exclude_uncertain=True)`.
   - **Verdict 3 — Unaccounted changes**: `recipes/patcher.py::ECUPatcher(
     stock_data, recipe).apply_all()` → predicted tuned bytes; `_diff_ranges(
     predicted, tuned)` (changed runs merged when < 16 bytes apart), minus
     volatile `_excluded_ranges`; each remaining range labelled by
     `maps/layout.py::segment(tuned_data)` + `find_ident_blocks(tuned_data)`
     (`ident` overlays win over sector regions).  If the recipe cannot apply
     (patcher raises `ValueError` / returns `None`) verdict 3 is skipped with a
     warning — the audit reports, it does not crash.
4. **Render** — JSON: `json.dumps(..., indent=2, sort_keys=True)` of
   `{stock, tuned, recipe, provenance{…}, fingerprint{…}, instruction_count,
   unaccounted{bytes, blocks[]}, clean, warnings}`.  Human: `✓ PASS / ✗ FAIL`
   lines per verdict, yellow unaccounted-block list, final
   `✅ The three artifacts are consistent.` / `⚠ Inconsistencies found…`.

## Expected output

**Human**:

```
  OpenRemap — Tune Audit
  stock.bin · stage1.bin · stage1.remap

  ✓ PASS  Provenance — recipe built from this stock (sha256 match)
  ✓ PASS  Fingerprint — recipe honestly describes the pair (match)
  ✓ PASS  Unaccounted — every changed byte is explained by the recipe.

  ✅ The three artifacts are consistent.
```

**JSON** — `stock`, `tuned`, `recipe` (paths), `provenance{ok,
recipe_sha256, stock_sha256}`, `fingerprint{ok, recipe, recomputed}`,
`instruction_count`, `unaccounted{bytes, blocks:[{offset, size, region,
region_confidence}]}`, `clean`, `warnings[]`.

**Exit codes:** `0` always when the audit completes — **verdict failures do NOT
change the exit code** (report, not a gate) · `1` recipe read/parse error,
non-recipe dict, or `ValueError` from `audit()` (size mismatch) · `2` missing
file arg.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | all 14 bin-reading CLI commands (`identify`, `analyze`, `audit`, `layout`, `merge`, `cook`, `tune`, `validate`, `checksum`, `health`, `scan-vins`, `scan-maps`, `diff-maps`, `routine`) + TUI (via `decode_image`) |
| `audit` | `recipes/audit.py` | CLI `audit` command only (not called by TUI or other commands) |
| `ECUDiffAnalyzer.build_recipe` | `recipes/recipe_builder.py` | `cook`, `cook-volatile`, TUI cook flow, `server` (deprecated), audit |
| `compute_fingerprint` | `recipes/recipe_builder.py` | `cook`, `cook-volatile`, `merge`, audit |
| `ECUPatcher.apply_all` | `recipes/patcher.py` | `tune`, TUI tune flow, `server` (deprecated), audit |
| `classify_volatile` | `recipes/volatile.py` | `cook-volatile`, audit |
| `segment` / `find_ident_blocks` | `maps/layout.py` | `analyze`, `health`, `layout`, `scan-maps`, `recipe_regions`, `identify/confidence`, `vin_scanner`, `volatile`, audit |
| `check_schema_version` | `recipes/recipe_builder.py` | validators, `tune`, `cook`, `cook-volatile`, `merge`, audit |

## Gotchas

- **Audit is not a safety verdict**: applicability to a different SW revision
  is `validate before`'s job; audit only checks that the three given artifacts
  are internally consistent — and even inconsistency exits **0**.
- Provenance compares the recipe's **`ecu.sha256` against the stock binary's
  hash** — not `match_key` (which may identify identically across revisions).
- Fingerprint re-cook runs with `require_unique=False` so non-unique-anchor
  recipes never abort the audit (that concern belongs to validate/tune).
- Volatile (4.5) recipes get **subset semantics**: the recipe is *allowed* to
  be a strict subset of the full diff (volatile exclusions), so exact
  fingerprint equality is replaced by self-consistency + subset + re-verify.
- A recipe that fails to apply to the stock makes verdict 3 **skip with a
  warning**, not a crash — `clean` can still be true via the first two verdicts.
- Unaccounted diff merges changed runs closer than 16 bytes (same policy as the
  cook diff) and labels ident-block ranges `ident` with confidence 0.5.
