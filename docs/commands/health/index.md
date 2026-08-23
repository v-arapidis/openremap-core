---
title: health
description: One-shot calibration health check — checksums, axis sanity, map counts, erased blocks, VINs. CI-gateable.
---

# health

The "check engine light" for a ROM file. Six checks in one command:
identity, checksums, axis sanity, map-count envelope, erased blocks,
VIN duplication.

## Quick start

```bash
openremap health stock.bin
openremap health stock.bin --json   # CI gate: exit 0 healthy / 1 issues
```

→ [health — advanced](advanced.md) — every flag, check semantics,
corpus-derived envelopes
