---
title: cook-volatile
description: Cook a car-portable recipe — exclude vehicle-specific bytes (VIN, checksum stores) with recorded evidence.
---

# cook-volatile

Like `cook`, but the recipe is built to **apply to other cars of the same
software revision** — not just the exact binary it was cooked from.

`cook` records *every* changed byte block. When the tune touched the VIN,
a checksum store, or a serial number, those blocks differ between cars —
so a plain recipe fails on another car of the same revision. `cook-volatile`
detects those volatile instructions at cook time, **excludes the
near-certain ones** (VIN records, verified checksum stores) from the patch
list, and records them in a `volatile` recipe section with evidence
(schema 4.5).

## Quick start

```bash
openremap cook-volatile stockA.bin stage1.bin --output portable.remap
```

The recipe applies to any car of the same SW revision whose calibration
bytes match the anchor windows.

→ [cook-volatile — advanced](advanced.md) — every flag, the volatile
section, evidence tiers, and how it differs from `cook`
