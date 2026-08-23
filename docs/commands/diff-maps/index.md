---
title: diff-maps
description: Compare two binaries at map level — match maps by axis fingerprint and diff cell-by-cell.
---

# diff-maps

Compare two binaries at the **map level**: match tables by axis
fingerprint, then diff cell-by-cell — which maps changed, by how much.

## Quick start

```bash
openremap diff-maps stock.bin stage1.bin
openremap diff-maps stock.bin stage1.bin --threshold 5 --json
```

→ [diff-maps — advanced](advanced.md) — every flag, grouping, recipe
annotation, exports
