---
title: layout
description: Flash-layout block map — where erased pages, code, calibration, and ident blocks start and end.
---

# layout

Segment a binary into flash-layout regions — `code`, `calibration`,
`erased`, `ident`, `mixed` — with per-region entropy, fill byte, and
confidence.

## Quick start

```bash
openremap layout ecu.bin
```

→ [layout — advanced](advanced.md) — every flag, JSON output
