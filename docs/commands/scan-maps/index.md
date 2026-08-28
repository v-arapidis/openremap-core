---
title: scan-maps
description: Structural scan — find calibration map axes and 2D tables without identification.
---

# scan-maps

Discover calibration maps structurally — no manufacturer identification
needed. Finds monotonic axes and the rectangular data blocks that follow
them, scores them, and (with `--classify`) labels them
probabilistically (`fuel 0.72`).

## Quick start

```bash
openremap scan-maps ecu.bin
openremap scan-maps ecu.bin --classify
openremap scan-maps ecu.bin --xrefs   # + code-reference signal (capstone)
```

→ [scan-maps — advanced](advanced.md) — every flag, regions, CSV export,
batch mode, code references
