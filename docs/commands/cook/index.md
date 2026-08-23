---
title: cook
description: Compare a stock and a tuned binary and save the difference as a portable .remap recipe.
---

# cook

Diff a stock binary against a tuned binary and save every change as a
`.remap` recipe (schema 4.4) — byte-level instructions plus the
calibration maps they touch.

## Quick start

```bash
openremap cook stock.bin stage1.bin --output recipe.remap
```

The recipe is portable, git-diffable, and applies to other binaries of
the same family.

**Applying to other cars?** If the tune touched the VIN or checksum
stores, a plain `cook` recipe fails on another car of the same revision
(those bytes differ between cars). Use
[cook-volatile](../cook-volatile/index.md) for a car-portable recipe that
excludes volatile bytes with recorded evidence.

→ [cook — advanced](advanced.md) — every flag, force/context options,
Guard-3 strictness
