---
title: Supported Manufacturers
description: The 6 ECU OEMs OpenRemap identifies — Bosch, Siemens, Delphi, Magneti Marelli, Denso, Hitachi — with per-family references.
---

# Supported Manufacturers

OpenRemap identifies ECU binaries from **six manufacturers** across
**35 extractors**. The registry is organised by ECU manufacturer (OEM) —
never by car brand — because the same OEM silicon appears across many
brands.

| Manufacturer | Extractors | Reference | Internals |
|---|---|---|---|
| **Bosch** | 18 | [Supported families](bosch/index.md) | [Extractor internals](bosch/internals.md) |
| **Siemens** | 6 | [Supported families](siemens/index.md) | [Extractor internals](siemens/internals.md) |
| **Delphi** | 2 | [Supported families](delphi/index.md) | — |
| **Magneti Marelli** | 4 | [Supported families](marelli/index.md) | — |
| **Denso** | 4 | [Supported families](denso/index.md) | Subaru applications (2001–2014) |
| **Hitachi** | 1 | [Supported families](hitachi/index.md) | Subaru applications (2013+) |

Each manufacturer page lists the supported ECU families with era, file
sizes, and vehicle coverage. Bosch has a per-family page for every family
group; the internals pages cover detection strategies, ident formats, and
extractor implementation details.
