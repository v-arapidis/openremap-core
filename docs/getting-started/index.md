---
title: Getting Started
description: Start here — what OpenRemap is, who it is for, and the fastest path from install to a working tune analysis.
---

# Getting Started

## What is OpenRemap?

OpenRemap is an **ECU binary intelligence toolkit**: it identifies ECU
firmware files, finds every byte a tune changed, packs the change into a
portable recipe, and applies it to another binary of the same family —
all offline, scriptable, and fully open.

It does **not** flash ECUs and does **not** correct checksums. It works
alongside your flashing and checksum tools.

→ The full story: [About OpenRemap](about.md)

---

## Who are you?

| You are… | Start here |
|---|---|
| 🏁 Just trying it out | [Install (2 min)](setup.md) → [Quick start (5 min)](quickstart.md) |
| 🔧 Tuning a car | [Quick start](quickstart.md) → [identify](../commands/identify/index.md) → [health](../commands/health/index.md) → [cook](../commands/cook/index.md) / [tune](../commands/tune/index.md) |
| 💻 Building tools on top | [Developer setup](install/developers.md) → [Architecture](../concepts/architecture.md) → [Contributing](../../CONTRIBUTING.md) |

---

## The five-minute path

1. **Install** — `uv tool install openremap` ([full setup](setup.md))
2. **Identify a file** — `openremap identify stock.bin`
   ([what to look for](../commands/identify/index.md))
3. **Health-check it** — `openremap health stock.bin`
   (checksums, axes, map counts, VINs — [details](../commands/health/index.md))
4. **Cook + tune** — follow the [quick start walkthrough](quickstart.md)
   (stock vs tuned → recipe → applied target)
5. **Fix checksums** — mandatory after any modification
   ([which schemes OpenRemap verifies](../commands/checksum/index.md))

---

## Command map

Every command supports `--help`. Full references live on each command's
page (simple + advanced).

| Command | What it does |
|---|---|
| `identify` | Read one binary — manufacturer, family, software, confidence |
| `health` | One-shot safety check — checksums, axes, map counts, VINs |
| `scan` | Batch-classify a folder of binaries |
| `checksum` | Verify known checksum schemes (detection only, no correction) |
| `scan-maps` | Find calibration maps and axes structurally |
| `diff-maps` | Compare two binaries at map level |
| `scan-vins` | Locate and score VIN candidates |
| `layout` | Flash-layout block map — code/calibration/erased/ident regions |
| `cook` | Diff stock vs tuned → `.remap` recipe |
| `merge` | Combine two recipes, validated against a common stock |
| `tune` | Validate → apply → verify, one shot |
| `validate` | Individual validation steps (before / check / after) |
| `audit` | The receipt check — do stock, tuned, recipe belong together? |
| `families` | List supported ECU families |
| `commands` | Cheat-sheet — one line per command |
| `workflow` | In-terminal step-by-step guide |

---

## Key concepts

| Concept | What it is |
|---|---|
| [How it works](../concepts/how-it-works.md) | The full pipeline — identify → health → checksum → cook → merge → tune → validate → audit |
| [Confidence scoring](../concepts/confidence.md) | How identification confidence tiers work — High → Unknown |
| [Recipe format](../concepts/recipe-format.md) | The `.remap` spec (schema 4.5 — volatile-aware) — byte-level changes + map annotations + identity |
| [Architecture](../concepts/architecture.md) | How the pieces connect — domains, extractor registry, Rust core |

---

## Supported ECUs

| Manufacturer | Families |
|---|---|
| [Bosch](../manufacturers/bosch/index.md) | 18 — EDC1 … MD1, ME7, Motronic, LH-Jetronic |
| [Siemens](../manufacturers/siemens/index.md) | 6 — Simtec 56 … EMS2000 |
| [Delphi](../manufacturers/delphi/index.md) | 2 — Multec, Multec S |
| [Magneti Marelli](../manufacturers/marelli/index.md) | 4 — IAW, MJD |
| [Denso](../manufacturers/denso/index.md) | 4 — SH7055, SH7058, SH72531, EE20 diesel (Subaru) |
| [Hitachi](../manufacturers/hitachi/index.md) | 1 — SH72546 (Subaru) |

---

## ⚠ Read this before flashing

OpenRemap produces **modified binaries** — it never flashes them. What
happens to the file next is your responsibility:

1. **Checksum correction is mandatory** after any modification —
   flashing an incorrect checksum can brick an ECU.
2. **Professional tuner review** — a recipe tells you what changed, not
   whether those changes are safe for your engine.

→ [Safety and intended use](../../DISCLAIMER.md)

---

## Where to get help

- `openremap workflow` — the in-terminal guide
- `openremap commands` — the cheat-sheet
- Issues and discussion: the project's GitHub repository
