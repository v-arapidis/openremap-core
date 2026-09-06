# `convert` — command summary (fast-lookup)

> One-file reference for
> `openremap convert <input> [-o out] [--format auto|ihex|srec|bin] [--json]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap convert <INPUT> [--output/-o PATH]
  [--format auto|ihex|srec|bin] [--json]`
- Registered in `openremap/core/cli/main.py` (import line 37,
  `app.command(name="convert")` block) →
  `openremap/core/cli/commands/convert.py::convert()`.
- Argument `input`: `exists, file_okay, dir_okay=False, readable,
  resolve_path` (missing file exits **2**).  `--output` is `writable`
  and `resolve_path`; `--format` is a plain string validated in-code
  against `_FMT_CHOICES = ("auto", "ihex", "srec", "bin")`.
- Purpose: normalise an ECU image to a flat raw binary.  Real Intel HEX
  and S-Record text is parsed (addresses + per-record checksums
  validated) and written as plain bytes; raw dumps pass through unchanged.

## Flow (top → bottom)

1. **`--format` validation** — value not in `_FMT_CHOICES` → styled error
   to stderr + exit **1**.
2. **Read raw bytes** — `input.read_bytes()`; `OSError` → styled error +
   exit **1**; empty file → "is empty" error + exit **1**.
3. **Decode** — `core/services/convert.py::decode_image(raw,
   force=force)` where `force = None` for `auto` else the format string:
   - `force == "bin"` → raw passthrough (`DecodeResult(raw, "binary")`).
   - auto sniff: first non-whitespace byte `:` → ihex, `S`+digit → srec,
     else raw binary passthrough.
   - bincopy parse (add_ihex/add_srec) validates per-record checksums.
     Corrupt-but-record-shaped input raises `ValueError` → styled error +
     exit **1**; a raw dump that merely starts with `:`/`S` falls back to
     raw binary *with a warning*.
   - Gaps between segments are filled with `0xFF` (bincopy default);
     an address span > `MAX_IMAGE_SPAN` (256 MB) raises `ValueError`
     (pathological-allocation guard); no data found → `ValueError`.
4. **Output path** — `output or (input.parent / f"{input.stem}.bin")`
   (default is NEXT TO THE INPUT, not the cwd).  `out_path.write_bytes(
   result.data)`; `OSError` → styled error + exit **1**.
5. **Warnings** — each `result.warnings` (e.g. "N data segments — gaps
   filled with 0xFF", raw-fallback note) printed to stderr in yellow.
6. **Render** — `--json`: payload dict (`input, format, format_name,
   output, size, address_min, address_max, segments, warnings`) via
   stdlib `json.dumps` with `sort_keys=True`.  Human: one summary line +
   `Saved to <path>`.

## Expected output

**Human:**

```
  Intel HEX            4,194,304 bytes  @ 0x800000-0xBFFFFF
  Saved to boot.bin
```

(`@ 0xMIN-0xMAX` omitted when `address_min is None`, i.e. raw binary.)

**JSON** (keys sorted): `input`, `format` (`"ihex"` | `"srec"` |
`"binary"`), `format_name` (`Intel HEX` | `Motorola S-Record` | `raw
binary`), `output`, `size`, `address_min`/`address_max` (absolute,
inclusive/exclusive; `null` for raw), `segments` (`0` for raw; `>1`
means gaps were 0xFF-filled), `warnings` (list).

**Exit codes:** `0` ok · `1` bad `--format` / read / empty / decode /
write error · `2` missing file.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `decode_image` | `core/services/convert.py` | `cli/io.load_binary_file` (every single-file bin-reading command: identify, checksum, layout, scan-vins, diff-maps, cook, cook-volatile, tune, audit, health, scan-maps, validate, merge, routine), batch loops `scan` + `scan_maps` dir mode, TUI (multiple decode sites in `tui/app.py`), `convert` itself |
| `DecodeResult` | `core/services/convert.py` | `cli/io.py`, `convert` |
| `encode_ihex` / `encode_srec` | `core/services/convert.py` | (not used by this command — available service helpers) |

## Gotchas

- `convert` is **deliberately NOT routed through `cli/io.load_binary_file`**
  — it reads raw bytes itself because it needs `--format`/`force` control
  and its own error wording.  A change to `io.load_binary_file` does not
  affect it.
- **Sniff-then-parse policy:** a file whose first byte looks like `:`/`S`
  but fails to parse is treated as raw binary *with a warning* (a raw dump
  can legitimately start with 0x3A/0x53); a file that structurally looks
  like the format but is corrupt raises `ValueError` (exit **1**).  Pass
  `--format` to skip sniffing entirely.
- `--format bin` forces raw passthrough; `--format ihex|srec` forces a
  strict parse (corrupt input always errors — no raw fallback).
- **Default output is `<input stem>.bin next to the input** — not the
  current directory.
- Gaps between HEX/SREC segments are filled with `0xFF` (erased flash,
  bincopy default) and reported in `warnings`/`segments` — converting a
  segmented image changes the byte count vs. the raw input.
- Address span > 256 MB is refused (`MAX_IMAGE_SPAN`) to avoid a
  pathological single-record-at-0xFFFFFFFF materialising 4 GB.
- Raw binary output: `address_min`/`address_max` are `None`,
  `segments = 0`, and the human line omits the `@` range.
