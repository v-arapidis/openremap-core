---
title: tune
description: One-shot — validate before, apply the recipe, verify after. Never writes a partial tune.
---

# tune

Apply a `.remap` recipe to a target binary in one command: validate
before → apply → verify after. A partial tune is never written; the
original file is never modified.

## Quick start

```bash
openremap tune target.bin recipe.remap --output target_tuned.bin
```

If Phase 1 fails, run `validate check` to diagnose why.

→ [tune — advanced](advanced.md) — every flag, report output, force
mode
