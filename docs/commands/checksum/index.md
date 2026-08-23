---
title: checksum
description: Verify known checksum schemes — OK/STALE detection, no correction.
---

# checksum

Detect which checksum schemes a binary satisfies and whether they
verify: Bosch ME7 (main/multipoint/rolling/multirange), IronFelix family
profiles, Siemens GS20/SMG2 + MS43, Denso Subaru descriptor tables.

Detection only — **no correction**. Correction stays with your
flashing/checksum tool.

## Quick start

```bash
openremap checksum ecu.bin
```

→ [checksum — advanced](advanced.md) — every flag, JSON output,
coverage notes
