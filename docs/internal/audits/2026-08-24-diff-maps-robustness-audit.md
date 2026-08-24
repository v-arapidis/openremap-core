# Diff-maps robustness audit — 0.7.1 (uncommitted)

**Status: ✅ Findings fixed** (audit 2026-08-24; one confirmed bug — strided
near-match mis-diff — fixed same day, plus minor items 1/2/4; item 3 logged as
a follow-up.  Verified on the real 4 MB EDC17 pair and the full suite.)

**Scope:** the uncommitted 0.7.1 addition — `diff-maps` robustness
(correlation near-match, correlation-refined suspicion, "changed but not
identified" report) and the scan/diff-maps calibration-region default
(`changelog/0.7.1.md`).  Changed files: `openremap/cli/commands/diff_maps.py`,
`openremap/cli/commands/scan_maps.py`, `tests/cli/test_cli_diff_maps.py`,
`tests/cli/test_cli_scan_maps.py`, `tests/conftest.py`, docs.

**Method:** full read of every changed file; targeted pytest
(`tests/cli/test_cli_diff_maps.py` + `test_cli_scan_maps.py` → **78 passed**);
end-to-end runs on the real 4 MB EDC17 tune pair
(`tests/data/tune/original.bin` + stage-1 file) in default / `--whole-file` /
`--json` / human / `--export` modes, a real 512 KB Subaru ROM, and an
all-erased fallback fixture.

---

## ✅ Verified working (real 4 MB EDC17 pair)

| Check | Result |
|---|---|
| Near-match catches rescaled axes | 6 near-matches, `r` = 0.9991–1.0, axis breakpoints shifted ~8% — exactly the intended catch |
| Correlation-refined suspicion | 7 maps with >90% changed cells, 0 flagged suspicious (heavy retune with `r≈1.0` recognised as legitimate) |
| "Changed but not identified" | 55 regions, whole-binary even with the calibration filter (as documented) |
| Calibration-region default | 304 tables hidden per side (default) vs 0 with `--whole-file`; counts consistent (2312 vs 2616 matched) |
| No-signal fallback | all-erased/tiny bin → `layout_filtered: false`, whole-file behavior |
| Backward compatibility | `--whole-file` reproduces the pre-change whole-file scan |
| JSON hygiene | `_fp` / `_stock_table` / `_tuned_table` stripped; `axis_stock` / `axis_tuned` / `correlation` serialize cleanly |
| Promotion vs unidentified ordering | promotion runs before the unidentified computation — no double counting |
| One-to-one matching | `used_stock_offsets` guard — no stock table consumed twice |
| `scan-maps` `top_score` pre-filter | **not a bug** — any table scoring ≥ 0.85 forces its sector to be labeled calibration, so a high-score table can never be hidden; top_score always equals the shown max |
| New test fixtures | `make_layout_bin` uses seeded `random.Random` (compliant with the CI no-`os.urandom` rule) |

---

## 🔴 Confirmed bug — strided (compound) near-matches are diffed with the wrong cells

`_near_match_pass` (`diff_maps.py`) ignores `stride`.  Compound tables (two
maps interleaved per row, `stride != None`) must be read with that stride —
the **exact-match** path does this correctly — but the near-match path calls
`_best_alignment(...)` **without** `stride` (contiguous read, mixing two
different maps' cells) and its shape guard does not compare `stride`.  The
match dict still reports `"stride": st.stride`, so the layout is right but the
numbers are wrong — and the correlation that decided the match was computed on
the wrong cells too.

On the real pair, 3 of the 5 near-matches are strided.  Measured before the
fix:

| Map | Reported (buggy) | Correct (strided) |
|---|---|---|
| `0x23C0A8` (5×5, stride 20) | changed 25/25, max 200.0, avg 134.64 | changed 20/25, max 138.0, avg 88.4 |
| `0x262554` (10×4, stride 28) | changed 30/40, avg 132.78 | changed 24/40, avg 108.58 |
| `0x23C09E` (5×5, stride 20) | avg 148.28 | avg 166.28 |

**Fix (applied):** `_near_match_pass` now compares `stride` in the shape guard
and mirrors the exact path — when `stride is not None` the cells are read
directly with `_read_cells(..., stride)` and the scanner offsets are trusted
(skip `_best_alignment`).  After the fix the real pair reports the correct
values (`0x23C0A8` → 20/25, max 138.0, avg 88.4; `0x23C09E` → avg 166.28);
the `0x262554` half now pairs as `0x262550` with 28/40 because the
correlation is computed on the correct cells.  Regression test added:
`test_strided_near_match_reads_cells_with_stride` (compound fixture with
rescaled axes, asserts 20/28 changed with stride 32).

---

## 🟡 Minor items

1. **Dead condition — fixed.** Near-match `suspicious` required `r < 0.7`
   (`_SUSPICIOUS_CORR`), but near-matches already require `r ≥ 0.95`, so it
   could never fire.  Near-matches now set `suspicious = False` by
   construction (a near-match is by definition strongly correlated, so never
   the "two different maps" case).
2. **`_axes_similar` tolerance documented wrong — fixed.** "within ~15% of
   the axis range" was inaccurate; the denominator is `max(max(a), max(b))`
   (the max *value*, not the range).  The docstring now states this and
   notes the `r ≥ 0.95` gate is the real safeguard.
3. **O(N²) scalability cliff — fixed (2026-08-24).** `_near_match_pass`
   was `unmatched_tuned × remaining_stock` with `_best_alignment` (25 cell
   reads) per axis-similar candidate.  Now pre-indexed by shape
   (cols/rows/cell_width/byte_order/stride — the exact shape-guard key),
   so the worst case is `unmatched × same-shape candidates`; a tune that
   rescales many axes no longer blows up quadratically.  Regression test:
   `test_many_same_shape_near_matches_pair_correctly` (4 same-shape maps
   with rescaled axes pair one-to-one with their own stock tables).
4. **"Every byte accounted for" is approximate — fixed.** The old
   any-overlap skip silently dropped the uncovered tail of a block
   straddling a table's edge.  `_unidentified_changed_blocks` now reports
   uncovered sub-ranges (interval subtraction), and `_covered_spans` pads
   spans by the ±4 pad-search slack so a matched table's alignment drift
   never turns its own changed cells into false unidentified tails.
   Real-pair unidentified count: 55 → 69 (straddling tails now visible).

---

## Recommendation

Verdict on the addition: correct, documented, and well tested — the
correlation refinement, calibration-region default with fallback, and the
unidentified-changes report all behaved as described on real binaries.  The
strided near-match bug was fixed with a regression test; minor items 1/2/4
are fixed.  Item 3 (N²) is a logged follow-up, not a blocker.

---

## Re-audit (2026-08-24, second pass)

All fixes verified on the real 4 MB EDC17 pair and the suite (128 CLI/unit
tests pass, incl. `test_strided_near_match_reads_cells_with_stride` and the
new `tests/recipes/` module; `cook-volatile` + volatile tests pass).

In this pass the 0.7.1 work also gained the **cook region tags** feature
(`openremap/core/services/recipes/recipe_regions.py` — the remaining
"layout consumers" roadmap item): every instruction gets a `region` field
(calibration / code / erased / mixed / unknown) and edits outside a
calibration region get a `CODE_AREA` flag + a portability warning.  Verified:
real pair → 38 calibration / 40 mixed / 1 erased, 41 `CODE_AREA`; flag shape
matches `InstructionFlag` (`kind`/`reason`/`confidence`/`action`); a
cook → `tune` round-trip reproduces the modified binary byte-identically
(0 differing bytes), so the tags are truly advisory and `tune` ignores them.

**New finding — performance regression (unconditional) — fixed:** 
`tag_instruction_regions` ran `scan_map_tables` (~2.9 s) + `segment` (~0.24 s)
≈ 3.1 s on every cook.  With the default `--annotate-maps`, `attach_maps`
*also* ran `scan_map_tables` with the same parameters, so the 4 MB map scan
happened **twice** — cook went ~3 s → ~10 s (default) / ~7 s
(`--no-annotate-maps`), measured 8.05 s / 5.6 s here.  Fix (applied): cook and
cook-volatile now run **one shared scan** (`scan_map_tables`, 0.55 / 16) and
pass the tables to both `attach_maps` and `tag_instruction_regions` (both
gained an optional `tables=` parameter; `attach_maps` keeps its internal scan
for other callers).  Re-measured: default cook **8.05 s → 5.70 s**
(pre-feature baseline 5.5 s + ~0.2 s segment).  Tests added for the
`tables=` reuse on both functions.  Minor from this finding: the summary
`tagged` count is now the number of instructions with an offset (an
`offset=None` instruction is skipped, not overcounted); the broad
`except Exception` in `_tag_regions` remains — acceptable for advisory
metadata, documented as hiding tagger bugs by design.

### Third pass (2026-08-24, final)

Shared-scan fix re-verified: default cook **10.18 s → 6.88 s** (double scan
removed); region output **unchanged** (38 calibration / 40 mixed / 1 erased,
24 maps, 41 `CODE_AREA`); cook → `tune` round-trip still byte-identical
(0 differing bytes).  `attach_maps`' internal scan params (0.55 / 16) match
the shared scan, so the reuse is behavior-preserving.  Full targeted suite:
**182 passed** (incl. `test_attach_maps_accepts_precomputed_tables` and the
strided near-match regression).  Changelog + cook docs now state the accurate
timing (default ~0.2 s marginal; `--no-annotate-maps` ~2.5–3 s).

**Final verdict: ship.** All confirmed findings are fixed and regression-tested.
Remaining logged (non-blocking) items: the O(N²) near-match scalability
follow-up is now also fixed (shape pre-index, `test_many_same_shape_near_matches_pair_correctly`);
the only open item is the absence of a `--no-region-tags` opt-out on the
`--no-annotate-maps` path (which still pays ~3 s for advisory tags).
