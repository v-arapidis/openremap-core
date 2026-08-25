# `scan` — command summary (fast-lookup)

> One-file reference for `openremap scan <dir> [--move] [--organize]
> [--recursive] [--report <csv|json>] [--dry-run]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap scan <DIRECTORY> [--move] [--organize] [--recursive]
  [--report PATH] [--dry-run]`
- Registered in `openremap/cli/main.py` via `app.command(name="scan")`
  → `openremap/cli/commands/scan.py::scan()`.
- Batch command — never aborts on a bad file; each file gets a row.

## Flow (top → bottom)

1. **Collect candidates** — files in `<dir>` matching `VALID_EXTENSIONS`
   (`{.bin,.ori,.hex,.s19,.srec,.mot}`, case-insensitive), optionally
   recursive.  Any other extension → **trash** row immediately.
2. **Per file** (the loop):
   - Read `filepath.read_bytes()`; OSError → **READ ERR** row, continue.
   - Decode `core/services/convert.py::decode_image(data)` (HEX/SREC
     sniff; raw passes through) → `ValueError` → **READ ERR** row.
   - Empty data → **trash** ("empty file") row.
   - `cli/commands/scan.py::classify_file(data, filename)` — runs **every**
     extractor (`manufacturers/__init__.py::EXTRACTORS`), not first-match:
     - 0 claimants → `unknown` · >1 claimants → `contested`
     - 1 claimant + no `match_key` → `sw_missing` · 1 claimant +
       `match_key` → `scanned`
   - `core/services/identify/confidence.py::score_identity(...)` for the
     tier (only where an extraction exists).
   - Destination routing → optional `safe_move(filepath, dest_dir)` when
     `--move`; `--organize` nests scanned/sw_missing into
     manufacturer/family trees.
   - `--report` accumulates `_build_report_row(...)` → CSV or JSON file.
3. **Summary** — per-destination counts + total.

## Expected output

**Human** — one coloured row per file (`SCANNED`/`SW MISSING`/`CONTESTED`/
`UNKNOWN`/`TRASH`/`READ ERR`/`EMPTY` tags), then a summary block.  **JSON
(`--report out.json`)** — a row per file with `filename, destination,
manufacturer, family, match_key, confidence, file_size, sha256`.

**Exit codes:** `0` on completion (even with trash/unknown files) · `1` on
unreadable/invalid directory (the `directory` arg has **no** `exists=True` —
the command validates it itself).

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `decode_image` | `core/services/convert.py` | all 13 bin-reading CLI commands, `convert`, `analyze`, TUI batch |
| `classify_file` | `cli/commands/scan.py` | TUI batch scan (`tui/app.py`) |
| `safe_move` | `cli/commands/scan.py` | TUI organize |
| `score_identity` | `core/services/identify/confidence.py` | `identify`, `analyze`, `health`, TUI |
| `EXTRACTORS` / `get_extractors` | `core/manufacturers/__init__.py` | `identify_ecu` (→ `identify`/`analyze`/`health`/cook), TUI |

## Gotchas

- **`classify_file` ≠ `identify_ecu`** — it runs ALL extractors (contested
  detection) and routes on `match_key`, not `software_version` (LH-Jetronic
  has no SW by design).  Never "fix" one to match the other.
- A broken extractor is swallowed per-file (`except Exception: pass`) —
  scan must never abort on an extractor bug.
- The extension gate drives the **trash classifier**, not the decode —
  `.hex` may be a raw Subaru dump (content sniff decides).
- Batch decode failures are one row, never an abort (READ ERR).
