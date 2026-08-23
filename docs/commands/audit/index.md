---
title: audit
description: The receipt check — do stock, tuned, and recipe belong together?
---

# audit

The receipt check of the ecosystem. Three verdicts:

1. **Provenance** — was the recipe built from THIS stock binary?
2. **Fingerprint** — is the recipe the honest record of this tune pair?
3. **Unaccounted changes** — bytes changed outside the recipe, labelled
   by layout region

## Quick start

```bash
openremap audit stock.bin stage1.bin recipe.remap
```

→ [audit — advanced](advanced.md) — every flag, JSON output
