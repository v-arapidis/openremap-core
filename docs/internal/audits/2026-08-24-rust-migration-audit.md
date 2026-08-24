# Rust migration audit

**Status: 🔍 In progress** — investigation 2026-08-24. Re-scan of the whole
codebase after the 2026-08-15 migration wave shipped in 0.7.0. Scope: what
is STILL Python-hot and worth moving to Rust purely for speed (effort is
explicitly not a constraint). All numbers re-measured today on the same
corpus as the previous audit; the previous document's verdicts that no
longer hold are called out explicitly.

Companion to: `2026-08-15-rust-migration-audit.md` (completed items),
`AGENTS.md` § Rust backend, `notes/report.md`.

---

## Methodology

1. cProfile every service-layer workload on representative inputs: the
   4 MB tune pair (`tests/data/tune/original.bin` + tuned), a 1 MB ME7.1
   (`tests/data/ECUs/Bosch/ME7.1/022906032CS.bin`), and the diff-maps /
   audit / volatile pipelines built on the 4 MB pair.
2. Isolated micro-benchmarks for the top suspects (checksum sweep's
   erased-check, `extract_raw_strings`, `count_unique_in_window`, the
   EDC17 pattern engine) with `timeit`-style repetition.
3. Where the previous audit claimed "CPython wins", re-benchmarked
   against the modern `memchr` crate's `memmem` (Two-Way/SIMD) — the
   old audit only tested a *naive* memchr loop.
4. Reproducible scripts: `benchmarks/profile_hotspots.py` (all
   workloads), `benchmarks/profile_micro.py`, `benchmarks/profile_regex.py`,
   `benchmarks/profile_audit.py`, `benchmarks/profile_diffmaps_cli.py`.

All wall-clock numbers below are from this machine (release Rust
extension, `abi3-py310`, Python 3.10.20).

---

## Already in Rust — not migration candidates

| Feature | Rust file | Measured today |
|---|---|---|
| Byte diff | `_rs/src/recipes/diff.rs` (`find_changed_blocks`) | ~102× (prior) |
| Shannon entropy | `_rs/src/primitives/entropy.rs` | 36–75× (prior) |
| Map hunt | `_rs/src/maps/map_hunter.rs` (`scan_map_axes`/`scan_map_tables`) | 4.7–5.0 s on the 4 MB pair — **now the single largest cost of layout/scan-maps/diff-maps; a Rust-side optimisation topic, not a migration** |
| Endianness | `_rs/src/identify/endian.rs` | ~542× incl. FFI (prior) |
| Ident-block scan | `_rs/src/maps/layout_scan.rs` | 9–10 ms on 4 MB |
| CRC-16/ARC | `_rs/src/primitives/crc16.rs` | ~30× incl. FFI (prior) |
| NefMoto locate + rolling | `_rs/src/checksums/nefmoto_scan.rs` | ~50–820× incl. FFI (prior) |
| Denso scan | `_rs/src/checksums/denso.rs` | ~110× (prior) |
| Checksum sweep compute | `_rs/src/checksums/checksum.rs` | 113 ms on 1 MB (the sweep's inner loop) |
| ME7 multipoint scan | `_rs/src/checksums/checksum.rs` | native |

---

## Candidates — measured 2026-08-24 (Python hotspot → Rust)

| # | Hotspot (module:function) | Workload | Cost | Rust port estimate | Notes |
|---|---|---|---|---|---|
| 1 | **Extractor pattern engine** — `manufacturers/base.py:_search` with lookaround patterns (EDC17 `calibration_id`, `vag/mercedes/bmw_part_number`, `ecu_variant_string`) | 75 calls over 4 MB EDC17 pair | **2.24 s** (cook) / 2.43 s (audit) — the #1 remaining hotspot in the whole app | ~20–100× (pattern rewrite + memmem/pos scan) | The old audit's "regex is C, don't migrate" verdict was measured on the 1 MB ME7 path where patterns are bounded. The 4 MB EDC17 path is different: full-binary scans with `(?<!…)`/`(?!…)` lookarounds cause catastrophic backtracking — 96–210 ms **per single scan** (`benchmarks/profile_regex.py`). Rust's `regex` crate cannot express lookbehind, so this is a *pattern rewrite + custom scan*, not a drop-in port. Biggest single win available. |
| 2 | `entropy.count_unique_in_window` / `find_unique_context` | 314 calls / 19,494 `bytes.find` per 4 MB cook | 0.49–0.52 s | algorithmic fix alone: **27× in pure Python** (9.27 s → 0.34 s on the pathological needle set); + memmem cap-at-2: ~0.08 s | **The old audit's "CPython bytes.find wins" verdict is stale.** True for *full counting* (memmem 8.96 s vs find 9.27 s — tie), but callers only ever test `==1` vs `>1` (`ctx_unique`, Guard 3, patcher ambiguity). Capping the count at 2 turns the worst case into an early exit: 27× with a 5-line Python change, more with Rust memmem. Decision-relevant semantics unchanged; error text "matches N times" would cap at 2 (see caveat). |
| 3 | `recipes/audit.py:_diff_ranges` | 1 call × 4 MB pair | 0.573 s | ~100× | Pure-Python byte loop (`for i in range(len(a))`) with the same 16-byte merge policy as `find_changed_blocks` — which already exists in Rust. Drop-in routing candidate, no new algorithm. |
| 4 | `manufacturers/base.py:extract_raw_strings` | 3 × 64 KB ident-block region | ~0.10 s per identify, 15.9 ms per 64 KB | ~10–30× | Pure-Python `for byte in data[region]` + `chr()` concatenation. A printable-run scan already exists in Rust (`layout_scan.rs`) — trivially reusable. |
| 5 | `checksums/checksum.py:_is_erased` (sweep) | 2,576 chunks × 3 `bytes.count` | 0.134 s of the 0.285 s 1 MB sweep | ~3–5× on the sweep | 7,728 C `count` calls driven from a Python loop. One Rust pass counting 3 candidate bytes per chunk replaces all of it. |
| 6 | `identify/vin_scanner.py:scan_vins` | 1 × 4 MB | 0.166 s self (finditer + `any()` genexpr 0.033 s + mirror `bytes.count` 0.016 s) | ~5–10× | Called 2× per `cook-volatile` and 1× per `cook` (annotator) — 0.19–0.39 s per command. Uses a fixed-width lookahead `(?=([A-Z0-9]{17}))`; a run-scan port avoids the regex entirely. |
| 7 | `maps/layout.py:segment` — `Counter(chunk)` dominant byte | 64 × 64 KB sectors on 4 MB | 0.185–0.20 s per segment | ~10× | `layout_scan.rs` already computes dominant-byte + count for ident runs; a per-sector variant absorbs this. `segment()` is called by `attach_maps`, `diff-maps`, and `audit` (each 4 MB pair). |
| 8 | `checksums/checksum.py:detect_me7` descriptor loop | 1 × 1 MB | 0.025 s (500 K × `struct.unpack_from("<4I")`) | ~100× | Small absolute cost, but a one-line delegation to a Rust scan; appears in every `verify_me7`/IronFelix/volatile path. |
| 9 | `cli/commands/diff_maps.py` cell machinery (`_best_alignment` 0.79 s, `_diff_cells` 0.23 s, `_read_cells` 0.12 s, `_pearson` 0.11 s) | 2,410–2,605 tables on 4 MB pair | ~1.2 s of the 5.9 s CLI | ~50–100× | The old audit's deferred item #10, now quantified. Still secondary to the 5.0 s Rust scan — but no longer negligible. |

### Whole-command impact (measured wall-clock today)

| Command | Today | With #1–#3 + #6 | Comment |
|---|---|---|---|
| `cook` (4 MB EDC17 pair) | 3.07 s | ~0.8–1.0 s | #1 (2.24 s) + #2 (0.49 s) + #6 (0.21 s) |
| `tune` (4 MB pair) | 3.08 s | ~0.8–1.0 s | same pipeline |
| `cook-volatile` (4 MB pair) | 3.38 s | ~0.9–1.1 s | cook + `classify_volatile` 0.30 s (two `scan_vins` → #6) |
| `audit` (4 MB pair) | 7.03 s | ~3.5–4 s | cook 3.2 s (#1/#2/#6) + `segment` 2.8 s (Rust scan 2.59 s + #7 0.20 s) + `_diff_ranges` 0.57 s (#3) |
| `checksum` (1 MB ME7.1) | 0.285 s | ~0.15–0.2 s | #5 (0.134 s) + #8 (0.025 s) |
| `identify` (1 MB ME7.1) | 0.35 s | ~0.2 s | #4 (~0.10 s) + `_search` 0.13 s (ME7 patterns are bounded; EDC17 is the pathological one) |
| `scan-vins` (4 MB) | 0.235 s | ~0.05–0.1 s | #6 |

---

## Where CPython still wins — do NOT migrate (re-verified 2026-08-24)

| Case | Verdict today |
|---|---|
| `bytes.find` full counting (`count_unique_in_window` *without* the cap) | Tie with `memmem` (9.27 s vs 8.96 s on the pathological set). **But the cap-at-2 fix (#2) changes the framing: the fix is algorithmic, and the Rust port is only the second half.** |
| `bytes.count` (VIN mirror counting, `_is_erased` inner) | C-speed per call; the cost is Python call overhead, not the scan. Fix = fewer calls (#5/#6), not a port of the primitive. |
| `ironfelix._sum8` / `_sum16le` | CPython `sum(bytes)` is C-speed; the Rust `sum8` has different semantics (mod-256 vs full-u32) — unchanged, still correct to keep in Python. |
| `struct.unpack_from` bulk reads (map cells, axes, stores) | C-backed; the Python cost is loop iteration over tables (covered by #9), not the unpack itself. |
| `patcher._find` (ctx+ob anchor, ±2 KB window) | Bounded C-backed `find`; negligible in profiles (tune 3.08 s shows no patcher hotspot). |
| `recipe_merge`, `preflight`, `recipe_maps`, `validate_*`, `confidence` scoring | Small instruction counts / dict work / C-backed compares — no byte-loop hotspot in any profile. |

---

## Recommended migration scope (per candidate)

1. **Extractor pattern engine** (`base._search` + EDC17-style patterns) —
   **MIGRATE.** Rewrite the lookaround patterns as boundary-checked scans
   (digit/alnum runs + neighbor checks) and run them natively, or port the
   `_search` loop to Rust operating on precomputed runs. This is the only
   candidate that moves the *whole-command* time on 4 MB EDC17 files by
   more than a factor of two. Parity strategy: compare extracted field
   sets per family on the 1,682-file corpus + synthetic edges, same as the
   endian/layout/nefmoto ports (2026-08-15). **Effort is the only cost —
   the win is the largest remaining one.**
2. **`count_unique_in_window` cap-at-2** — **DO IT IN PYTHON FIRST** (5
   lines, 27× on the pathological case, zero FFI risk), then optionally
   port to Rust memmem for the residual ~4×. Consumers: `find_unique_context`
   (recipe builder), `recipe_merge` Guard 3, patcher ambiguity. Caveat:
   Guard-3 / patcher messages render "matches N times" — cap the displayed
   count at 2 ("matches ≥2 times") or keep exact counts only when small.
3. **`audit._diff_ranges` → `find_changed_blocks`** — **MIGRATE** (routing,
   no new algorithm). Verify merge-boundary parity (both use 16-byte
   merge) with the existing audit tests.
4. **`extract_raw_strings`** — **MIGRATE** (printable-run scan; reuse
   `layout_scan.rs` logic). Parity: string lists must match exactly,
   including `strip()` and dedup semantics.
5. **`_is_erased`** — **MIGRATE** (single-pass 3-byte count). Parity: the
   `>= 0.9` ratio rule is trivial to replicate; sweep results must be
   byte-identical (corpus + synthetic).
6. **`scan_vins`** — **MIGRATE** (run-scan + scoring). Parity: VINHit
   offsets/confidences/evidence must match; covered by existing
   `scan-vins` corpus tests. Watch the WMI/check-digit/year tables — they
   are data, port verbatim.
7. **`segment` dominant byte** — **MIGRATE** (extend `layout_scan.rs`).
   Must preserve `Counter.most_common(1)` tie semantics (insertion order).
8. **`detect_me7` descriptor loop** — **MIGRATE** (trivial).
9. **diff-maps cell machinery** — **MIGRATE AFTER #1–#3.** The Rust scan
   dominates, so the payoff is real but second-order; keep the deferred
   status from the 2026-08-15 audit until the map-hunt cost itself is
   addressed.

## Rust-side optimisation topics (not migrations)

- `scan_map_tables` (2.33–2.59 s per 4 MB) is now the biggest single cost
  in `layout`, `scan-maps`, `diff-maps`, and `audit`. The Python side is a
  thin wrapper; any further gain is inside `_rs/src/maps/map_hunter.rs`.

## Non-candidates (measured, no migration value)

- patcher / validators / recipe_merge / recipe_maps / preflight /
  map_classifier / map_exporter — small instruction counts or C-backed
  primitives; no byte-loop hotspot in any profile.
- CLI rendering, JSON output, schema handling, `health` checks — I/O or
  dict bound.
- `layout.segment` entropy per sector — already Rust; the Python residue
  is only the dominant-byte `Counter` (#7).
