---
title: Quick Start
description: The five-minute path — install, identify a file, health-check it, cook a tune into a recipe, and apply it.
---

# Quick start

You just installed OpenRemap — here's how to do something useful in the next
five minutes. No theory, just commands and what to expect.

> Files can be `.bin`, `.ori`, or `.hex` — all are read the same way.

---

## Step 1: Check what you have

Point `identify` at any ECU binary to see what's inside:

```bash
openremap identify ecu.bin
```

The output prints the **manufacturer**, **ECU family**, **software version**,
**hardware number**, **calibration ID**, and a **confidence tier** that tells
you how reliably the file was identified. Tiers are **High**, **Medium**,
**Low**, **Suspicious**, or **Unknown** — High means all key identifiers were
found and consistent. (Not sure how to read the tiers? → [Confidence
scoring](../concepts/confidence.md))

## Step 2: Health-check it

Before doing anything else with the file, run the one-shot safety check:

```bash
openremap health ecu.bin
```

It verifies the known checksums, sanity-checks map axes, compares the map
count against the family's expected envelope, looks for erased blocks
embedded in data, and flags duplicate VINs. Output is `ok`/`warn`/`fail`
per check; the command exits 0 when healthy, 1 when something fails —
usable in scripts and CI.

## Step 3: Scan a folder

Preview everything in a directory at once:

```bash
openremap scan ./my_bins/
```

This prints a table of every recognised binary — no files are moved. When you're
ready to organise, add the flags:

```bash
openremap scan ./my_bins/ --move --organize
```

Files are sorted into `manufacturer/family/` subfolders automatically.

---

## Step 4: Cook a recipe

Diff a stock binary against a tuned binary to capture the changes:

```bash
openremap cook stock.bin tuned.bin --output recipe.remap
```

The `.remap` file is a portable JSON recipe (schema 4.5 — volatile-aware; `cook` emits 4.4 with maps, lean 4.3 without) that records every
byte-level difference, annotates which calibration maps the changes touch,
and embeds the identity metadata of both files. You can share it, version
it, or apply it to other binaries in the same ECU family.

---

## Step 5: Apply a recipe

Apply a recipe to a target binary:

```bash
openremap tune target.bin recipe.remap
```

This runs a 3-phase process — **validate** the recipe against the target,
**apply** the patch, and **verify** the result. If anything looks wrong,
it stops before writing.

To run just the pre-flight check without applying anything:

```bash
openremap validate before target.bin recipe.remap
```

> ⚠ After any modification, **recalculate the ECU checksums** with your
> flashing/checksum tool before flashing — OpenRemap verifies but does
> not correct them. See [How it works — safety](../concepts/how-it-works.md).

---

## What's next

| Topic | Where to go |
|---|---|
| How everything fits together | [How it works](../concepts/how-it-works.md) |
| Full command reference | [CLI reference](cli.md) |
| Interactive terminal UI | Run `openremap` with no arguments, or `openremap-tui` |
| Confidence scoring explained | [Confidence scoring](../concepts/confidence.md) |
| Recipe file spec | [Recipe format](../concepts/recipe-format.md) |
| Supported ECU families | Run `openremap families` — 35 extractors across 6 manufacturers |
| Guided walkthrough | Run `openremap workflow` for a step-by-step guide in your terminal |
