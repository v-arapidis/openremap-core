---
title: analyze
description: Describe a whole ECU binary in one pass — identity, VIN, flash layout, maps, checksums, health verdict.
---

# analyze

One command that describes a whole ECU binary: container + hardware,
identity + confidence, VIN, flash layout, map discovery, checksums, and
the health verdict.

`identify` answers *"what ECU is this?"* — `analyze` answers *"tell me
everything about this dump."*

## Quick start

```bash
openremap analyze stock.bin
openremap analyze stock.bin --fast       # skip the slow sections (~1-2 s)
```

→ [analyze — advanced](advanced.md) — every flag, JSON schema, speed budget
