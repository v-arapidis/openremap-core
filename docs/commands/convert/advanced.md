---
title: convert — advanced
description: Normalise ECU binary images — every flag, JSON output, format override, gaps and checksums.
---

# `openremap convert`

Normalise an ECU binary image to a flat raw binary:

```bash
openremap convert <INPUT> [-o OUTPUT] [--format auto|ihex|srec|bin] [--json]
```

## What it fixes

`.hex` used to be accepted as a *raw byte dump*.  That is correct for the
Subaru (RomRaider) corpus — those files ship as raw binaries named
`.hex` — but real Intel HEX / S-Record files are **text**: records with
byte counts, absolute addresses, data, and per-record checksums.  Feeding
that text to the analyser as raw bytes silently produces garbage.
`convert` (and every binary-reading command) now sniffs the file
**content**:

| First bytes | Verdict |
|---|---|
| `:` | Intel HEX → parsed (addresses + checksums validated) |
| `S` + type digit | Motorola S-Record → parsed |
| anything else | raw binary → passed through unchanged |

The raw-`.hex` Subaru dumps never start with `:`/`S`, so their behaviour
is byte-identical.  A raw dump that *happens* to start with `:`/`S` but
does not parse is kept raw with a warning; a file that structurally looks
like HEX/SREC but fails its checksum is a loud error.

## Flags

| Flag | Meaning |
|---|---|
| `-o, --output <path>` | Write the flat binary here.  Default: `<input stem>.bin` next to the input. |
| `--format <mode>` | `auto` (sniff content — default), `ihex`, `srec` (force that format, strict), or `bin` (force raw, skip sniffing). |
| `--json` | Emit the summary as JSON. |

## JSON output

```json
{
  "input": "boot.hex",
  "format": "ihex",
  "format_name": "Intel HEX",
  "output": "boot.bin",
  "size": 4194304,
  "address_min": 0,
  "address_max": 4194304,
  "segments": 1,
  "warnings": []
}
```

`address_min` / `address_max` are the absolute record range (base kept —
a file based at `0x80000000` reports that base; the written image is
base-normalised to start at 0).  `segments > 1` means the file had gaps,
which are filled with `0xFF` (erased flash) and reported in `warnings`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Read / decode / write error (bad checksum, no data, empty file, span > 256 MB, invalid `--format`) |
| 2 | Input file missing (Click `exists=True`) |

## Examples

```bash
# Normalise a bootloader image
openremap convert boot.hex -o boot.bin

# S-Record, JSON summary
openremap convert flash.s19 --json

# A raw dump that starts with ':' — force raw so it isn't mis-sniffed
openremap convert weird.bin --format bin -o out.bin

# Strict parse: fail loudly on any corrupt record
openremap convert boot.hex --format ihex -o boot.bin
```
