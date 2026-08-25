---
title: scan-vins
description: Locate VIN candidates in an ECU binary and score them — WMI, check digit, year, mirror counts.
---

# scan-vins

Find VIN candidates and score them on structural evidence — never a
bare claim: WMI whitelist, ISO 3779 check digit, model-year character,
numeric tail, ident-block context, mirror consensus.  Every candidate is
also **decoded** (vininfo): WMI → manufacturer, region, country, and
model years — labelled *decoded, unverified*.

## Quick start

```bash
openremap scan-vins ecu.bin
openremap scan-vins ecu.bin --min-confidence 0.6
```

→ [scan-vins — advanced](advanced.md) — every flag, evidence fields,
JSON output
