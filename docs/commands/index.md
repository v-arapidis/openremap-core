# `openremap commands`

Print a compact one-line-per-command cheat-sheet of every available command.
Designed for returning users who know the workflow and just need a quick
reminder of the exact syntax.

---

Every command has two pages: a **simple** introduction and an
**advanced** reference with every flag and example.

| Command | What it does | Simple | Advanced |
|---|---|---|---|
| `identify` | Read one binary — manufacturer, family, software, confidence | [index](identify/index.md) | [advanced](identify/advanced.md) |
| `health` | One-shot safety check — checksums, axes, map counts, VINs | [index](health/index.md) | [advanced](health/advanced.md) |
| `checksum` | Verify known checksum schemes (no correction) | [index](checksum/index.md) | [advanced](checksum/advanced.md) |
| `scan` | Batch-classify a folder of binaries | [index](scan/index.md) | [advanced](scan/advanced.md) |
| `scan-vins` | Locate and score VIN candidates | [index](scan-vins/index.md) | [advanced](scan-vins/advanced.md) |
| `layout` | Flash-layout block map | [index](layout/index.md) | [advanced](layout/advanced.md) |
| `scan-maps` | Structural map discovery | [index](scan-maps/index.md) | [advanced](scan-maps/advanced.md) |
| `diff-maps` | Compare two binaries at map level | [index](diff-maps/index.md) | [advanced](diff-maps/advanced.md) |
| `cook` | Stock vs tuned → `.remap` recipe | [index](cook/index.md) | [advanced](cook/advanced.md) |
| `cook-volatile` | Car-portable recipe — excludes volatile bytes (VIN, checksum stores) | [index](cook-volatile/index.md) | [advanced](cook-volatile/advanced.md) |
| `merge` | Combine two recipes against a common stock | [index](merge/index.md) | [advanced](merge/advanced.md) |
| `tune` | Validate → apply → verify, one shot | [index](tune/index.md) | [advanced](tune/advanced.md) |
| `validate` | Individual steps — before / check / after | [index](validate/index.md) | [advanced](validate/advanced.md) |
| `audit` | The receipt check — stock, tuned, recipe | [index](audit/index.md) | [advanced](audit/advanced.md) |
| `families` | List supported ECU families | [index](families/index.md) | [advanced](families/advanced.md) |
| `workflow` | In-terminal step-by-step guide | [guide](workflow.md) | — |

---
