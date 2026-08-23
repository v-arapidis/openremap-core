---
title: merge
description: Combine two recipes into one, validated against a common stock binary.
---

# merge

Combine two recipes (e.g. `egr_off.remap` + `stage1.remap`) into one,
validated against a common stock binary. Same-offset conflicts are
reported for you to resolve; nothing is guessed.

## Quick start

```bash
openremap merge a.remap b.remap --stock stock.bin -o both.remap
```

→ [merge — advanced](advanced.md) — conflict handling, strict mode,
maps re-annotation
