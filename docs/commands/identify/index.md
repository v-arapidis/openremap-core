---
title: identify
description: Identify an ECU binary — manufacturer, family, software version, hardware, calibration ID, and confidence.
---

# identify

Read an ECU binary and tell you what it is: manufacturer, ECU family,
software version, hardware number, calibration ID — and how confident
the match is (High → Unknown, with the evidence behind it).

## Quick start

```bash
openremap identify stock.bin
```

## What to look for

- **Match key** (`FAMILY::SOFTWARE`) — the identity fingerprint used by
  every downstream command
- **Confidence tier** — High means all key identifiers were found and
  consistent; Suspicious means stop and check

→ [identify — advanced](advanced.md) — every flag, JSON schema, exit
codes, scripting examples
