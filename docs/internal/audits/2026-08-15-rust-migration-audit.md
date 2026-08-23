# Rust migration audit

**Status: ✅ Completed** (investigation 2026-08-15; the recommended
migrations — endian, ident-block scan, NefMoto locate+rolling, CRC-16/ARC,
Denso scan — shipped with parity verification in 0.7.0.  Remaining
candidates are noted inside.)

Investigation date: 2026-08-15. Scope: every CLI feature (`identify`,
`scan`, `cook`, `tune`, `validate`, `audit`, `layout`, `checksum`,
`scan-maps`, `diff-maps`, `merge`, `scan-vins`, `families`), profiled with
cProfile and re-measured wall-clock, then benchmarked 1:1 against
standalone Rust ports (release build, opt-level 3 + LTO) over the same
real corpus files. Parity of every Rust port was verified against the
Python result before trusting its timing.

This is an investigation document — nothing below is a commitment to
migrate. Each candidate is discussed individually with the user before
any code moves.

---

## Methodology

1. cProfile each command on representative inputs (1 MB ME7.1, 4 MB tune
   pair, 512 KB MS43, corpus batches).
2. Re-measure every hot function in isolation with `timeit` (profiler
   overhead inflates byte-loop costs ~6-10×).
3. Port the hot loop to Rust in a scratch crate (`rustbench`) — exact
   algorithm, exact masks/tables, same input bytes.
4. Verify output parity (offsets, sums, CRCs, block lists, cell counts).
5. Compare timings; only parity-verified numbers are reported here.

Inputs used:

| Name | File | Size |
|---|---|---|
| ME7 | `tests/data/ECUs/Bosch/ME7.1/8D0907551M-0001.bin` | 1 MB |
| TUNE pair | `tests/data/tune/original.bin` + tuned | 4 MB each |
| MS43 | `tests/data/ECUs/Siemens/MS43/MS43_WBABW510X0PK46741_430069_512KB.bin` | 512 KB |

---

## Already in Rust — not migration candidates

| Feature | Rust file | Notes |
|---|---|---|
| Map hunting | `_rs/src/map_hunter.rs` | `scan_map_tables`/`scan_map_axes` — dominates scan-maps/diff-maps (4.7 s on the 4 MB pair). Possible *Rust-side* optimisation topic, not a migration item. |
| Checksum sweep | `_rs/src/checksum.rs` | 11+ algo families. |
| Byte diff | `_rs/src/diff.rs` | `find_changed_blocks` (cook/audit). |
| Shannon entropy | `_rs/src/entropy.rs` | `shannon_entropy` / `is_low_entropy`. |

---

## Candidates — Python hotspot vs Rust port

All Rust numbers are parity-verified against the Python output.

| # | Hotspot (module:function) | Workload | Python | Rust | Speedup |
|---|---|---|---|---|---|
| 1 | `identifier._detect_endian` | 256 KB u16 word scan | 118.7 ms | 0.147 ms | **~807×** |
| 2 | `nefmoto.rolling_checksum` | 3 × 1 MB byte-wise rolling hash | 1928 ms | 9.5 ms | **~203×** |
| 3 | `nefmoto._locate_pattern` (seeds) | masked scan, 1 MB, step 2 | 137.5 ms | 0.31 ms | ~445× |
| 4 | `nefmoto._locate_pattern` (cksm) | masked scan, 1 MB | 17.7 ms | 0.034 ms | ~520× |
| 5 | `nefmoto._locate_pattern` (ranges_m) | masked scan, 1 MB | 133.3 ms | 0.36 ms | ~370× |
| 6 | `nefmoto._locate_pattern` (ranges_c) | masked scan, 1 MB | 293.4 ms | 0.78 ms | ~375× |
| 7 | `nefmoto._locate_pattern` (mr_func) | masked scan, 1 MB | 286.8 ms | 0.68 ms | ~420× |
| 8 | `layout.find_ident_blocks` | printable-run scan, 4 MB | 252 ms | 8.2 ms | **~31×** |
| 9 | `ms43.crc16_arc` | 3 × 64 KB table CRC | 40.9 ms | 0.53 ms | ~77× |
| 10 | `diff_maps` cell machinery | 25 shift combos × 8×8 u16 grid | 0.21 ms/pair | 0.002 ms/pair | ~105× |
| 11 | `checksums.denso` scan | descriptor-table scan, 1 MB | 1.1 s | 10 ms | **~110×** |

### Whole-command impact

- **`checksum`** (1 MB ME7.1): 1.09 s real today. `_locate_pattern` calls
  (~0.87 s combined) + `rolling_checksum` are almost all of the NefMoto
  portion. Rust ports → est. ~0.15-0.25 s total.
- **`identify`** (1 MB): 0.47 s real; `_detect_endian` ≈ 119 ms (~25% of
  runtime) + ident-block scan. Rust → ~0.2 s saved per call.
- **`scan`** (corpus: 1126 files / 198 s): `scan` does NOT call
  `identify_ecu()` — no endianness detection. Per-file cost on 1 MB files
  is ~34% `find_ident_blocks` (via the confidence cross-check), the rest
  extractor loops + hashing + process startup (fixed ~0.35 s). Candidate
  #8 is the whole story here.
- **`audit`** (4 MB pair): profile shows `find_ident_blocks` as the #1
  cost (two 4 MB scans) + `_detect_endian`. Rust → ~0.7 s saved.
- **`diff-maps`** (4 MB pair): 5.8 s real = Rust map hunt (4.7 s) +
  Python cell machinery (~0.5-1 s). #10 is a modest but real gain.
- **`scan-vins`** (4 MB): 389 ms = `find_ident_blocks` (252 ms) + candidate
  scoring. #8 covers most of it.

---

## Where CPython already wins — do NOT migrate

These were benchmarked and deliberately left in Python:

| Case | Why CPython wins |
|---|---|
| `entropy.find_all_offsets` / `count_unique_in_window` | CPython `bytes.find` is Two-Way/FASTSEARCH C code; beats a naive Rust memchr loop for context-anchor workloads. Already documented in `entropy.py`. |
| `bytes.count` (VIN mirror counting) | C-backed memmem; a naive Rust `windows().position` loop measured ~2.8 ms per 4 MB — comparable, not better. A memchr crate would win only marginally; not worth a dependency for this path. |
| `ironfelix._sum8` / `_sum16le` / multirange | CPython `sum(bytes)` is C-speed. The existing Rust `sum8` also has different semantics (mod-256 vs full-u32 accumulator) — see `ironfelix.py` notes. |
| Extractor regex scans (`base._search`) | The `re` engine is C; residual cost is Python-side match iteration, which is an algorithmic (bounded regions) improvement, not a Rust one. |
| `struct.unpack_from` bulk cell reads | C-backed; the strided/compound path is where Python loops exist, covered by #10 above. |

---

## Recommended migration scope (per candidate)

1. **`identifier._detect_endian`** — ✅ **MIGRATED 2026-08-15**
   (`_rs/src/endian.rs`, exposed as `openremap._rust.detect_endian`).
   Parity verified 1:1 against the Python heuristic on 1,693 entries
   (1,682 real corpus files + 11 synthetic edge cases) — zero
   mismatches. Python implementation removed; a thin wrapper keeps the
   module API stable. Final numbers: 118.7 ms → 0.219 ms/op (~542×
   including FFI); `openremap identify` wall time 0.47 s → 0.35 s
   (−26%). Subaru SH7058 correctly detected big-endian, ME7.1
   little-endian.
2. **`layout.find_ident_blocks`** — ✅ **MIGRATED 2026-08-15**
   (`_rs/src/layout_scan.rs`, exposed as
   `openremap._rust.find_ident_blocks`). Returns (start, end, dominant
   byte, count, Shannon entropy) per run; Python keeps `Region`
   construction + rounding only (the regex/Counter implementation is
   erased). Dominant-byte ties replicate `Counter.most_common(1)`
   insertion-order semantics. Parity verified on 1,693 corpus files +
   9 synthetic edge cases (13,486 blocks) — zero mismatches. Final
   numbers: 252 ms → 8.8 ms/op on 4 MB (~29× incl. FFI + Region
   build); `scan` batch (12 × 1 MB) 1.94 s → 1.25 s (−36%);
   `audit` 4 MB pair −0.6 s; `identify` 0.35 s → 0.30 s.
3. **`nefmoto._locate_pattern` + `rolling_checksum`** — ✅ **MIGRATED
   2026-08-15** (`_rs/src/nefmoto_scan.rs`, exposed as
   `openremap._rust.locate_pattern` / `rolling_checksum`). Python byte
   loops erased; wrappers keep the module API. Function-level parity on
   236 ME7.x corpus files × 7 patterns + synthetic edges (windows, steps,
   masks, init variants, idx-guard): zero mismatches. Pipeline-level
   parity: `detect_me7_rolling` + `detect_me7_multirange` results
   byte-identical on all 236 files. Final numbers: locate patterns
   17.7–293 ms → 0.11–2.2 ms (~170–820× incl. FFI); rolling 3 × 1 MB
   1928 ms → 38.7 ms (~50× incl. FFI). The `checksum` CLI on a 1 MB
   ME7.1 now spends ~6 ms on NefMoto (was ~1.1 s Python); the remaining
   wall time is the unchanged sweep (0.6 s, Rust) + IronFelix sums
   (0.34 s, CPython `sum` — see "CPython wins" above) + multipoint scan.
   Note: the pre-migration CLI wall of 1.09 s recorded during the audit
   is not reproducible — it conflicts with the component sums and is
   treated as an environment artifact; per-function timeit numbers are
   the trustworthy metrics.
4. **`ms43.crc16_arc`** — ✅ **MIGRATED 2026-08-15**
   (`_rs/src/crc16.rs`, exposed as `openremap._rust.crc16_arc`). Python
   byte loop and the cached `_arc_table` helper erased; wrapper keeps the
   module API. Parity verified against the standard CRC-16/ARC check
   value ("123456789" → 0xBB3D), 12 synthetic edges (block guards, init
   masking, overlap, split-equals-full), and `detect_ms43` pipeline
   results on the 4-file MS43 corpus (base 3/3 ok, tuned 2/3 with
   calibration stale) — zero mismatches. Final numbers: 40.9 ms →
   1.38 ms per 3 × 64 KB op (~30× incl. FFI); MS43 checksum CLI 0.9 s
   total on 512 KB (sweep + multipoint dominate).
5. **`diff_maps` cell machinery** (`_read_cells` strided path,
   `_best_alignment`, `_diff_cells`) — the port is more involved because
   the surrounding matcher is Python dataclass logic. Defer until the
   map-hunt Rust cost is addressed; the 4.7 s native scan dominates the
   command anyway.
6. **`checksums.denso` scan** — ✅ **MIGRATED 2026-08-15**
   (`_rs/src/checksums/denso.rs`). The Denso Subaru descriptor-table
   scanner (prefix sums + structural filter + bounded run walk) was
   implemented in Python first (validated on a 6-file sample + 191-file
   parity against the Rust port), then ported 1:1. Python loop erased;
   the wrapper builds `DensoChecksumInfo` dataclasses only. Final
   numbers: 1.1 s → 10 ms per 1 MB (~110×); `checksum` CLI on a 1 MB
   Subaru file 3.27 s → 1.29 s (rest is sweep + IronFelix + startup).
   Note: a first attempt seeded runs at any structurally-plausible
   entry and built dataclasses during the walk — quadratic on diesel
   firmware (75,680 plausible positions). Fixed by seeding only at
   verifying entries and bounding the backward walk; this is the
   classic "profile the pathological case before calling it done"
   lesson.

## Open questions for the per-feature discussion

- FFI overhead: candidates 1/2/8/9 are single-call-per-file — negligible
  overhead. Candidate 3 is 5-11 calls per file — still fine. Confirm
  per-call overhead target (< 50 µs) when wiring.
- Whether `find_ident_blocks`' dominant-byte computation must stay exact
  (Counter semantics) or can be simplified.
- For `diff_maps`: is the real fix Rust-side map-hunt optimisation
  instead of the Python cell loops?

## Non-candidates (no migration value)

- patcher / validate_strict / validate_patched / recipe_merge /
  recipe_maps / annotator / map_classifier / confidence — small
  instruction counts or dict/JSON-bound; profiling shows no byte-loop
  hotspot.
- `layout.segment` sector loop — entropy per sector is already Rust;
  sector overhead is negligible.
- CLI rendering, JSON output, schema handling — I/O bound.
