# `analyze` — command summary (fast-lookup)

> One-file reference for `openremap analyze <file> [--json] [-o out]
> [--fast] [--no-maps]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Registered in `openremap/core/cli/main.py` via `app.command(name="analyze")`
  → `openremap/core/cli/commands/analyze.py::analyze()`.
- Flags: `--json`, `--output/-o`, `--fast` (skip maps+checksums+health),
  `--no-maps` (skip map scan only).  Argument `file`: `exists,
  file_okay, readable, resolve_path` (missing → exit **2**).

## Flow (top → bottom)

1. **Read + decode** — `core/cli/io.py::load_binary_file(path, "Binary")` →
   `core/services/convert.py::decode_image` (HEX/SREC sniff); format code →
   `core/cli/io.py::CONTAINER_NAMES` (shared with `identify`).
2. **Compose** — `core/services/analyze.py::analyze_binary(data, filename,
   *, fast, skip_maps, container)` → `AnalyzeReport` (one pass):
   - `identify/identifier.py::identify_ecu` → identity dict
   - `identify/vin_scanner.py::scan_vins(min_confidence=0.6)` →
     `vin_decode.py::decode_vin` (mirrors `identify`)
   - maps: `maps/map_hunter.py::scan_map_axes` → `scan_map_tables` (whole
     file, per plan §4A) → `maps/layout.py::segment(data, tables=…)`
     (reuses the scan — no second pass) + `find_ident_blocks`
   - **xrefs (code-reference signal)**: `arch/__init__.py::arch_for_family`
     (family → arch tuple) → `code_regions_from_layout(regions)` →
     `collect_xrefs(data, codes, arch, endian, spans=…)`
     (`core/arch/refs.py`; capstone disassembly, statically-resolvable
     references only, data-driven load-base detection).  When
     `arch_for_family` returns `None` (unknown family), falls through to
     `core/arch/detect.py::detect_arch(data, codes, endian, spans)` — the
     CPU-detection cascade (trial-decodes c166/tricore/sh, first whose
     references hit table spans wins, fork-isolated against decoder crashes)
     → `adjust_table_scores(tables, xr)` re-ranks with the bonus when
     ``status == "ok"``
   - checksums: `checksums/checksum.py::sweep` + `verify_me7`,
     `checksums/denso.py::detect_denso`, `checksums/ms43.py::detect_ms43`,
     `checksums/ironfelix.py::detect_ironfelix` → compact summary dict
     (`schemes`/`me7`/`denso`/`ms43`/`ironfelix`)
   - coherence: `services/coherence.py::check_coherence(ident, checksums,
     xrefs)` → agree/stale/gap/conflict verdict (conflict = the only hard red
     flag)
   - `identify/confidence.py::score_identity(..., coherence=…)` — coherence
     fed in as evidence (agree +10, conflict −15 + warning) when
     checksums/xrefs were computed (else `None` → unchanged)
   - health: `health.py::health_report` (reused wholesale — re-scans
     internally; accepted double-scan for v1)
3. **Render** — human `_render()` (sectioned: container/identity/
   confidence/coherence/VIN/layout/maps/xrefs line/checksums/health); JSON
   `AnalyzeReport.to_dict()` via stdlib `json.dumps`.  Write via
   `_write_output`-style block; analysis exception → exit **1**.

## Expected output

**Human** — sectioned report (see the [analyze advanced](https://docs.openremap.com/commands/analyze/advanced) wiki page);
ident blocks capped at 8 + "… N more"; maps top-5 by score with a
`⟶code` marker on xref-referenced tables; a "code refs" line under Maps
(e.g. `code refs: 1,213 reference(s) from 588,466 instructions (tricore,
base 0x80000000, …)`) or `skipped (<reason>)`; a **Coherence** line
(`Coherence: checksum ✓ arch ✓`, coloured green/neutral/red by status);
fast-mode warning line at the end.

**JSON** — 14 top-level keys: `container, file_size, sha256, identity,
confidence, coherence, vin, hardware, layout, xrefs, maps, checksums,
health, fast`.  `coherence` is the agree/stale/gap/conflict verdict
(`status`/`checks`/`conflict`) or `null` in `--fast`/`--no-maps`.
`xrefs` is a summary dict (`status/skip_reason/arch/endian/base_address/
code_bytes_scanned/insn_count/reference_count`) or `null` when maps were
skipped.  `maps.tables` capped at 50 by score, each with an `xref`
evidence block (`referenced_by_code/data_refs/axis_refs/insns`);
`checksums`/`health` are `null` in `--fast`.

**Exit codes:** `0` always on success (descriptive — warnings don't fail);
`1` read/decode/analysis error; `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `load_binary_file` / `CONTAINER_NAMES` | `core/cli/io.py` | all 13 bin-reading CLI commands + TUI; `identify` |
| `decode_image` | `core/services/convert.py` | batch loops (`scan`, `scan_maps` dir, TUI scan), `convert`, `identify` |
| `identify_ecu` | `core/services/identify/identifier.py` | `identify`, `health`, `scan_maps`, cook (`recipe_builder`), tune/validate (`preflight`), TUI |
| `score_identity` | `core/services/identify/confidence.py` | `identify`, `scan`, `health`, TUI |
| `scan_vins` | `core/services/identify/vin_scanner.py` | `identify`, `scan-vins`, `health`, cook annotator, `cook-volatile` |
| `decode_vin` | `core/services/vin_decode.py` | `identify`, `scan-vins`, `health` |
| `scan_map_axes` / `scan_map_tables` | `core/services/maps/map_hunter.py` | `scan-maps`, `diff-maps`, `cook` (attach_maps), `health` |
| `segment` / `find_ident_blocks` | `core/services/maps/layout.py` | `layout`, `scan-maps`, `diff-maps`, `cook` regions, health |
| `arch_for_family` | `core/arch/__init__.py` | `analyze`, `scan-maps`, `tests` |
| `collect_xrefs` | `core/arch/refs.py` | `analyze`, `scan-maps --xrefs`, `tests` |
| `detect_arch` | `core/arch/detect.py` | `analyze`, `scan-maps --xrefs` |
| `adjust_table_scores` / `xref_evidence` | `core/services/maps/xrefs.py` | `scan-maps --xrefs`, cook (`attach_maps(xrefs=…)`) |
| `check_coherence` | `core/services/coherence.py` | `analyze` |
| `sweep` / `verify_me7` / `detect_denso` / `detect_ms43` / `detect_ironfelix` | `core/services/checksums/*` | `checksum`, `health` |
| `health_report` | `core/services/health.py` | `health` (CLI), `analyze` |

## Gotchas

- **`--fast` skips `health_report` entirely** — do not call it in fast mode
  (it re-runs the ~9 s scans internally and would defeat the flag).
- The map scan is whole-file (plan §4A) — tables in code/erased sectors
  are included; `scan-maps`' calibration-region default is NOT applied.
- **xrefs adds ~4 s on the 4 MB EDC17** (capstone decode of ~2.6 MB of
  code).  It is presence-only: a table is only ever *boosted* by the
  bonus, never demoted; unknown families fall through to the
  CPU-detection cascade (fork-isolated, so a decoder crash rejects a
  candidate rather than killing the process).
- **`coherence` is `None` in `--fast`/`--no-maps`** — it needs checksums +
  xrefs, both of which those flags skip; `score_identity` then runs with no
  coherence evidence (byte-identical to pre-coherence output).
- Serialisation stays **stdlib json** (byte-stability rule) — `to_dict()`
  is the single JSON-safe conversion point.
- `analyze` is descriptive, never a gate — `health` owns exit-code
  verdicts for CI.
