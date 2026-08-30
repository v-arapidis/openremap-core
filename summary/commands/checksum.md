# `checksum` — command summary (fast-lookup)

> One-file reference for `openremap checksum <file> [--json]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.  The canonical background is `notes/checksums/checksums.md` —
> read it before any checksum work.

## Entry & registration

- Command: `openremap checksum <FILE> [--json]`
- Registered in `openremap/core/cli/main.py` (import line 45,
  `app.command(name="checksum")` block) →
  `openremap/core/cli/commands/checksum.py::checksum_cmd()`.
- Argument `file`: `exists, file_okay, dir_okay=False, readable,
  resolve_path` (Click enforces existence → missing file exits **2**).
- Detection only — **no correction** (stale results are reported, never fixed).

## Flow (top → bottom)

1. **Read + decode** — `cli/io.py::load_binary_file(file, "Binary")` →
   `core/services/convert.py::decode_image(raw)` (content-sniffs Intel
   HEX/S-Record; raw dumps pass through).  Read/decode/empty errors →
   styled stderr + exit **1**.
2. **Generic sweep** — `core/services/checksums/checksum.py::sweep(data)`
   → Rust `openremap._rust.checksum_compute`.  Closed config space: 11
   algo families × init × final-xor × region (whole / 16/32/64 KB pages,
   tail exclusions) × store (file_end / page_end, LE/BE) × direct /
   two's-complement.  Returns `SchemeMatch` list sorted by page rate.
   Whole-file match = weak (one store location); a page scheme matching
   ≥ 90% (`_PAGE_RATE`) of non-erased pages is the strong signal.
   Erased pages (≥ 90% single-byte fill: 0x00/0xFF/0xC3) are excluded
   from page statistics.
3. **ME7 main** — `checksum.py::verify_me7(data)` →
   `detect_me7` (4×u32-LE descriptor run scanned at 2-aligned offsets +
   `(v, ~v)` pair at `end − 0x20`); recomputes the u32-accumulated LE u16
   sum over the two descriptor blocks (Rust) → `ChecksumVerdict("ok" |
   "stale")` or **None** when the structure is absent.
4. **ME7 multipoint** — `detect_me7_multipoint(data)` (Rust
   `me7_multipoint_scan`): self-validating 16-byte descriptors
   (start/end/checksum/~checksum) — no whitelist.  Only run when
   `me7 is not None`.  `detect_me7_multipoint_unverified(data)`: valid
   pair but non-verifying — bootrom descriptors (`start < 0x20000`
   counted into `mp_bootrom`; the bootrom is not in a flash-only dump).
5. **IronFelix profiles** — `core/services/checksums/ironfelix.py::
   detect_all(data)` (imported as `detect_ironfelix`): runs every family
   detector in registry order (`vag_me7xx, me3x, m797, m798, china797,
   me745, samand, gs20, smg2`), each wrapped in try/except.
6. **NefMoto ME7** — `nefmoto.py::detect_me7_rolling(data)` (seed-table
   hash, inverted u32 stores) and `detect_me7_multirange(data)` (u32
   byte sum stored as `(v, ~v)`) — C166 pattern/descriptor scans.
7. **MS43** — `ms43.py::detect_ms43(data)`: CRC-16/ARC over the three
   descriptor slots (boot @0x3C24, program @0x6FDE0, calibration
   @0x73FE0; program blocks are memory addresses, file = addr − 0x80000)
   + the two 32-bit monitor slots (@0x6FDAE / @0x72FFC) reported
   `unverified` (runtime checks).
8. **Denso** — `denso.py::detect_denso(data)` (Rust `detect_denso`):
   12-byte BE32 `[start][end][diff]` descriptor table (~0xFFB80),
   **end-inclusive** sum, target `0x5AA5A55A`; `[0,0]` = disabled entry.
9. **Render** — `--json`: one dict with per-family blocks + `schemes`.
   Human: section per family (coloured OK/STALE lines, monitor slots in
   cyan, IronFelix profile table) then the generic-match table
   (`Algo/Init/Xor/Region/Store/Form/Pages`); "strong" = `rate >= 0.9`
   (`_RATE`) **and** a page region (green form column, else yellow).
   Empty match list → just the header, no table.

## Expected output

**Human:**

```
  OpenRemap — Checksum Detection
  ecu.bin  •  4,194,304 bytes  •  2 known family scheme(s)  •  0 generic match(es)

  Bosch ME7 main checksum: OK  (stored 0F80F452, expected 0F80F452)
  Bosch ME7 multipoint: 4 block(s) verify, 1 bootrom descriptor(s) not verifiable from a flash-only dump
  Siemens MS43 CRC16: 2/3 sections ok
      calibration: stale (stored 0xB3A1, expected 0x9F04)
      cal monitor sum @0x72FFC: runtime check (not verifiable from a static dump)
```

**JSON** (when `--json`; keys sorted) — `file`, `file_size`, and nullable
per-family blocks: `me7_main{status,stored,expected}`, `me7_multipoint
{valid,bootrom_unverifiable}`, `me7_rolling[{store,status,stored,expected,
ranges[],init_range}]`, `me7_multirange{...}`, `ms43{crcs[],mons[],ok,
total}`, `denso{table,status,ok,total,entries[]}`, `ironfelix[{family,
description,subtype,checks[],checks_ok,checks_total,multipoint_valid,
multipoint_unverified}]`, `schemes[{algo,init,final_xor,region,
exclude_tail,store,store_le,complement,pages_matched,pages_total,rate}]`.

**Exit codes:** `0` ok · `1` read/decode error · `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` | `cli/io.py` | every single-file bin-reading command (identify, analyze, layout, scan-vins, diff-maps, cook, cook-volatile, tune, audit, health, scan-maps, validate, merge, routine) + TUI (via `decode_image`) |
| `sweep` | `core/services/checksums/checksum.py` | `analyze` service (`core/services/analyze.py`) |
| `verify_me7` | `core/services/checksums/checksum.py` | `analyze`, `health`, IronFelix profiles (`ironfelix.py`), `volatile.collect_checksum_stores` (via `detect_me7`) |
| `detect_me7_multipoint` / `_unverified` | `core/services/checksums/checksum.py` | `health`, IronFelix profiles, `volatile.collect_checksum_stores` |
| `detect_ironfelix` (`detect_all`) | `core/services/checksums/ironfelix.py` | `health`, `analyze`, `volatile.collect_checksum_stores` |
| `detect_me7_rolling` / `detect_me7_multirange` | `core/services/checksums/nefmoto.py` | `health`, `volatile.collect_checksum_stores` |
| `detect_ms43` | `core/services/checksums/ms43.py` | `health`, `analyze`, `volatile.collect_checksum_stores` |
| `detect_denso` | `core/services/checksums/denso.py` | `health`, `analyze`, `volatile.collect_checksum_stores` |

## Gotchas

- **Whole-file match is weak evidence** (one store location); only a
  per-page scheme matching ≥ 90% of non-erased pages is a strong signal.
  Most freely-downloaded dumps carry stale/stripped checksums and yield
  no matches (ISSUE-3) — an empty result is expected, not a bug.
- **MS43 monitor sums are runtime checks** — always reported
  `unverified`, never `stale` (they sum runtime XRAM `_mon` values;
  tuners disable via `lc_swi_cal_mon_cks`).  MS43 program blocks are
  memory addresses (file = addr − 0x80000).
- **Denso `end` is INCLUSIVE** — the sum runs `[start, end+1)`.
  RomRaider's Java port (end-exclusive) reports factory files stale by
  the trailing word; the community DLL and factory files agree with
  inclusive-end.  Do not "fix" this.
- **ME7 rolling** seed table is the CRC-32/IEEE table and the store is
  inverted; bootrom descriptors are unverifiable from a flash dump
  (community tools whitelist them).
- IronFelix quirks: `SummInt8` accumulates a full u32 (not mod 256);
  `SummInt16Intel` pairs an odd trailing byte with the byte AFTER the
  region; the GS20 "corrected" corpus file stores its CRC byte-swapped
  (documented quirk, not a detector bug).
- Family detectors are guarded by structure: `verify_me7` returning
  None disables the whole ME7 block (multipoint/rolling/multirange).
