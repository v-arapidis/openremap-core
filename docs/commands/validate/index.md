---
title: validate
description: Individual validation steps — before (pre-flight), check (diagnostic), after (post-tune confirmation).
---

# validate

Run the validation steps individually instead of through `tune`:

| Mode | What it checks |
|---|---|
| `validate before` | original bytes at every recorded offset (pre-flight) |
| `validate check` | search the whole binary — EXACT / SHIFTED / MISSING (diagnostic) |
| `validate after` | modified bytes present at every expected offset (confirmation) |

## Quick start

```bash
openremap validate before target.bin recipe.remap
openremap validate check  target.bin recipe.remap   # when before fails
openremap validate after  tuned.bin recipe.remap
```

→ [validate — advanced](advanced.md) — every flag, JSON output, exit
codes
