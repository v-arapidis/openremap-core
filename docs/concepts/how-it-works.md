---
title: How It Works
description: The full OpenRemap pipeline — identify, health-check, verify checksums, cook, merge, tune, validate, audit — step by step, with the safety rules.
---

# How It Works

OpenRemap is a pipeline. Each step is independent — you can run them
separately, inspect the output at every stage, and automate the whole
chain. The full picture:

```
identify → health → checksum → cook → merge → tune → validate → audit
   │          │        │       │      │       │        │        │
 read the   check    verify   diff    combine recipes  apply   confirm   receipt
 binary    the file  sums     stock   into one        the     it landed  check
                               +tuned                  recipe  correctly
```

---

## 1. Identify

Before anything else, OpenRemap reads the binary and figures out what it is.

```bash
openremap identify ecu.bin
```

It scans the file through a registry of manufacturer-specific extractors
(35 across 6 OEMs: Bosch, Siemens, Delphi, Magneti Marelli, Denso,
Hitachi) and pulls out everything it can find: ECU family, software
version, hardware number, calibration ID. From those it builds a
**match key** — a compact identity string that uniquely represents this
binary:

```
Manufacturer       Bosch
ECU Family         EDC17
ECU Variant        EDC17C66
Software Version   1037541778126241V0
Match Key          EDC17C66::1037541778126241V0
```

Every identification also carries a **confidence tier** (High → Unknown)
with the evidence behind it — so you know *how sure* the tool is, not
just what it guessed.

## 2. Health-check

A one-shot safety pass over the file: checksums, axis sanity, map-count
envelope, erased blocks, VIN duplication.

```bash
openremap health ecu.bin
```

`health` fails the gate when any check fails — usable in CI. It is the
"check engine light" for a ROM file.

## 3. Verify checksums

OpenRemap detects every known family checksum scheme and reports
OK/STALE — detection only, **no correction**.

```bash
openremap checksum ecu.bin
```

Coverage today: Bosch ME7 (main, multipoint, rolling, multirange),
IronFelix family profiles, Siemens GS20/SMG2 and MS43, and Denso
Subaru descriptor tables. After any modification, checksum correction
remains **your tool's job** (see Safety below).

## 4. Cook — the diff

```bash
openremap cook stock.bin stage1.bin --output recipe.remap
```

OpenRemap compares the two files byte by byte, groups consecutive
changed bytes into blocks, and records each block as an **instruction**:
offset, original bytes (`ob`), modified bytes (`mb`), and a context
anchor (`ctx`) used during patching.

The output is a `.remap` JSON recipe — human-readable,
version-controllable, self-contained, with the ECU identity embedded.
Today's format is **schema 4.4**: alongside the byte-level instructions,
cook annotates *which calibration maps* each instruction touches (the
`maps` layer — structural descriptors with axis values and probabilistic
labels). A git review of a tune reads "fuel base map +20%, 3 cells"
instead of hex soup — and the patcher still operates purely on
`instructions`, so a 4.4 recipe patches byte-identically to a 4.3 one.

→ [Recipe format — full spec](recipe-format.md)

## 5. Merge (optional)

Combine two recipes built from the same family — e.g. `egr_off.remap` +
`stage1.remap` — validated against a common stock binary, with conflicts
reported for you to resolve.

```bash
openremap merge a.remap b.remap --stock stock.bin -o both.remap
```

## 6. Tune — validate before → apply → verify after

```bash
openremap tune target.bin recipe.remap --output target_tuned.bin
```

- **Validate** — every instruction's original bytes must be at its
  recorded offset in the target.
- **Apply** — writes `mb` bytes using a `ctx + ob` anchor search
  (±2 KB): maps that shifted slightly between software revisions are
  still found and tuned correctly.
- **Verify** — the modified bytes must be present at every expected
  offset. A partial tune is never written; the original file is never
  modified.

If `validate before` fails, run the diagnostic to find out why:

```bash
openremap validate check target.bin recipe.remap
```

It reports every instruction as **EXACT** (right place), **SHIFTED**
(found elsewhere — the map moved between revisions), or **MISSING**
(wrong ECU, already modified). This answers the question: *is this the
right ECU, or just the wrong revision?*

## 7. Audit — the receipt check

```bash
openremap audit stock.bin stage1.bin recipe.remap
```

Three verdicts: was the recipe built from THIS stock (provenance), is
the recipe the honest record of this tune pair (fingerprint), and are
there changed bytes the recipe does not explain (unaccounted changes)?

---

## The match key — why it matters

Every recipe embeds the match key of the binary it was built from, and
every validator checks the match key of the target. The key is built
from the ECU family and the software version:

```
EDC17C66::1037541778126241V0
  ↑            ↑
  family        software version
```

Two ECUs from the same car model, even the same year, can have different
software versions — the maps sit at different offsets. A recipe built
from version A applied to version B can write bytes to the wrong
location entirely.

A match-key mismatch is not a hard block — you can override it — but it
is a serious warning. Unless you have confirmed through
`validate check` that the instructions land correctly on the target, a
mismatch means stop.

For ECU families where no software version is readable from the binary,
the match key falls back to another extracted field (calibration ID,
hardware number). The patcher still works, but the identity guarantee
is weaker.

---

## When would you actually use this

### Two ECUs with the same software version

The most common scenario. You tuned one ECU and a second customer has
the same car, same family, same software. Instead of starting from
scratch, cook a recipe from the first pair and apply it to the second
ECU — validated before anything is written.

### You want to know what a tune actually changes before flashing it

A tune you bought, a file from a forum. Run `openremap cook` with the
stock and the modified file: every changed offset, every original byte,
every modified byte. You decide if you trust it.

### You are iterating on a calibration

At the end of each session, cook a recipe between the previous version
and the new one — an exact, git-diffable record of what changed.

### You are porting a tune across software revisions

Same family, minor revision difference. Run `validate check` first:
SHIFTED results mean the anchor search may recover the offsets;
MISSING instructions mean the maps moved too far — stop there.

### You want to batch-organize a library of binaries

```bash
openremap scan ./my_bins/                    # preview — nothing moves
openremap scan ./my_bins/ --move --organize  # sort into Bosch/EDC17/ etc.
```

---

## ⚠ Safety — mandatory before flashing

> **1. Checksum correction.** OpenRemap verifies checksums but does not
> correct them. Every ECU has internal checksums that must be
> recalculated after any binary modification. Use a dedicated checksum
> tool (WinOLS, ECM Titanium, or the appropriate standalone corrector
> for your ECU family). Flashing a binary with an incorrect checksum
> **will brick your ECU.**
>
> **2. Professional tuner review.** A recipe tells you what bytes
> changed — it does not tell you whether those changes are safe for
> your specific engine, fuel quality, hardware condition, or use case.
> Before flashing any modified binary to a vehicle, the tune must be
> reviewed and approved by a qualified, experienced tuner.
>
> OpenRemap is a tool for applying and auditing binary changes. The
> responsibility for what those changes do to an engine rests entirely
> with the person who created the tune and the professional who
> validated it.

→ [Disclaimer](../../DISCLAIMER.md)

---

## See also

- [Recipe format](recipe-format.md) — the `.remap` spec
- [Confidence scoring](confidence.md) — tiers, signals, evidence
- [Getting started](../getting-started/index.md) — install + quick start
- [About OpenRemap](../getting-started/about.md) — the project's identity and aims
