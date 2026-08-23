---
title: Evidence
description: How OpenRemap explains its results — detection evidence tags, confidence signals, probabilistic labels, and provenance.
---

# Evidence

OpenRemap explains **why** it believes something, not just what it
believes. Every layer of the pipeline carries evidence that you can
inspect:

| Layer | Evidence example |
|---|---|
| Detection | `SIZE_MATCH`, `MAGIC_MATCH`, `IDENT_BLOCK`, `FAMILY_STRING` — what made an extractor claim a binary |
| Identification | confidence tier + signal list (each with its point delta) |
| Checksums | per-entry verdicts: `ok` / `stale` / `disabled`, with stored vs expected values |
| Maps | structural score, classifier label + probability (`fuel 0.72`), layout region |
| Health | per-check verdicts (`ok` / `warn` / `fail` / `skip`) with details |
| VINs | WMI whitelist, ISO check digit, year, numeric tail, mirror count, ident context |

The principle: **a verdict without evidence is a guess.** Anything
probabilistic must say how confident it is and what supports it.

---

## Detection evidence

When an extractor's `can_handle()` decides a binary belongs to a family,
it records *why* as evidence tags:

| Tag | Meaning |
|---|---|
| `SIZE_MATCH` | File size matches a known family size |
| `MAGIC_MATCH` | Header / descriptor magic bytes match |
| `IDENT_BLOCK` | Identity block found at the expected offset |
| `FAMILY_STRING` | Family-specific ASCII string found (e.g. `Copr.DENSO`) |
| `POINTER_TABLE` / `LAYOUT_FINGERPRINT` / `BOOT_BLOCK` | Structural layout evidence |
| `EXCLUSION_CLEAR` | No conflicting family signatures found |

Tags are weighted — structural ones (magic, ident block) are worth more
than soft ones (size match). The confidence scorer uses the tag list to
compute a dynamic detection-quality bonus.

## Confidence signals

`score_identity` turns the identity dict plus the detection evidence into
a score and a tier (High / Medium / Low / Suspicious / Unknown). Each
contributing factor is a **signal** with a point delta:

```
  Tier   MEDIUM
  Signal  +13  detection evidence (4 tags: SIZE_MATCH, MAGIC_MATCH, …)
  Signal  +30  canonical SW version
  Signal  +10  calibration ID present (86CAU_AT)
  Signal  -25  tuning/modification keywords in filename
```

Negative signals matter as much as positive ones — a filename saying
`sport` or `stage1` costs points, and the warning is shown.

## Probabilistic labels

Where a real name cannot be proven (no A2L/DAMOS), labels stay
**probabilistic**: the map classifier emits `fuel 0.72`, never a bare
claim. The number says how well the structure matches the label's
profile; the label is a suggestion, not a fact.

## Provenance

Every result records where its information came from:

- **Identity fields** — extracted from the binary at known offsets (the
  extractor states them).
- **Checksums** — from community-documented algorithms (RomRaider,
  NefMoto, IronFelix, EcuFlash defs), validated against factory files.
- **Map labels** — inferred structurally; an A2L/DAMOS source would
  upgrade them, never be replaced silently.
- **Verdicts** — always explainable by the evidence list above.

## Where evidence is exposed

| Command | Where to look |
|---|---|
| `identify` | the confidence section (tier, score, signals, warnings) |
| `scan` | per-file confidence in the table and reports |
| `checksum` | per-scheme and per-entry statuses with stored/expected values |
| `health` | per-check verdicts with details |
| `scan-maps --classify` | `label` + `label_confidence` per table |
| `scan-vins` | per-candidate evidence flags and confidence |

→ [Confidence scoring](confidence.md) — how tiers and signals are computed
→ [How it works](how-it-works.md) — the pipeline that produces the evidence
