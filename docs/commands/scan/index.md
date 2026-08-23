---
title: scan
description: Batch-classify a folder of ECU binaries — preview, move, or organize into a manufacturer/family tree.
---

# scan

Classify every ECU binary in a folder through all registered extractors.
Preview mode is the default — nothing moves.

## Quick start

```bash
openremap scan ./my_bins/                    # preview — nothing moves
openremap scan ./my_bins/ --move --organize  # sort into Bosch/EDC17/ etc.
```

Files sort into `scanned` / `sw_missing` / `contested` / `unknown` /
`trash`; `--organize` adds the `manufacturer/family` tree.

→ [scan — advanced](advanced.md) — every flag, reports (JSON/CSV),
move modes
