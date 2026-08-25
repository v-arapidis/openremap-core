---
title: convert
description: Normalise ECU binary images — real Intel HEX and S-Record files become flat raw binaries.
---

# convert

Turn any ECU binary image into a flat raw binary.  Real **Intel HEX**
(`.hex`, `.ihex`) and **Motorola S-Record** (`.s19`, `.srec`, `.mot`)
files are text — each line carries an address and a per-record checksum.
`convert` parses them into plain bytes; raw dumps pass through unchanged.

## Quick start

```bash
openremap convert boot.hex -o boot.bin
openremap convert flash.s19 -o flash.bin
```

Every command that reads a binary (identify, cook, tune, scan-maps, …)
does this automatically — the file's **content** is sniffed, not its
extension — so a real Intel HEX file just works everywhere.  `convert`
is for when you want the flat bytes explicitly.

→ [convert — advanced](advanced.md) — every flag, JSON output, format override
