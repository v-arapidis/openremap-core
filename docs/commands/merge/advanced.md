---
title: merge — advanced
description: Combine two recipes against a common stock — every flag, conflicts, examples.
---

# `openremap merge`

Combine two recipes built from the same family of originals into one — like
git's three-way merge: the stock binary is the common ancestor, recipe A is
"ours", recipe B is "theirs".

Use this to compose small, reusable mods (egr_off, stage1, dpf_off) into one
recipe per car instead of keeping monolithic per-car recipes.

---

## Usage

```bash
openremap merge <A.remap> <B.remap> [--stock original.bin] [-o merged.remap] [--strict]
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--stock <path>` | (required unless recipes share `ecu.sha256`) | The common stock binary. Every instruction from both recipes is validated against it. |
| `--output`, `-o` | stdout | Write the merged recipe to this file. |
| `--strict` | off | Abort instead of skipping instructions that don't match the stock. |

---

## Merge rules

| Situation | Result |
|---|---|
| Same offset, same values | One copy kept |
| Same offset, different values | **Conflict** — same address, different edit; a human decides |
| Overlapping ranges, different boundaries | **Conflict** — edit boundaries disagree; a human decides |
| Different, non-overlapping offsets | Both kept |

## Why the stock is the merge base

Neither tuner needs the other's exact original file. Every instruction from
both recipes is validated against your stock (ob-at-offset). A recipe built
from a slightly different original (e.g. VIN area differs) fails only for the
instructions that truly differ — those are reported and skipped (or abort
with `--strict`).

Without `--stock`, the merge requires both recipes to declare identical
`ecu.sha256` + `match_key`.

The merged recipe re-checks anchor uniqueness (Guard 3) against the stock
and re-annotates the maps layer (schema 4.4) when `--stock` is given.

---

## Example

```bash
# Both tuners worked from your stock.bin
openremap merge egr_off.remap stage1.remap --stock stock.bin -o both.remap

  ✅ Merged egr_off.remap (12 instructions) + stage1.remap (67 instructions) → 79 instructions.
  ⚠  egr_off.remap: 1 instruction(s) skipped — they do not match the stock binary
     (…). This recipe was likely built from a slightly different original.

  Saved merged recipe to both.remap (schema 4.4)
```

---

## Related commands

| Command | Reference |
|---|---|
| `openremap cook` | [→ cook.md](../cook/index.md) — build a recipe |
| `openremap tune` | [→ tune.md](../tune/index.md) — apply a recipe |

---

