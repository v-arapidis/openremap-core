---
title: About OpenRemap
description: What OpenRemap is, the gap it fills, why it is open source, and what the project aims to be.
---

# About OpenRemap

## The problem with how tuning works today

When you load a tune into WinOLS, ECM Titanium, or any professional calibration tool, those tools are doing something sophisticated: they interpret the binary. They know where the maps are, what the axes mean, what the values represent. That knowledge is valuable, and those tools have earned their place in professional workshops.

But there is a gap they do not fill.

When you take a modified binary and want to know *exactly* what changed — at the byte level — and move that change reliably to another ECU with the same software, there is no clean, open, scriptable way to do it. You are either eyeballing hex diffs, using proprietary scripts, or hoping the checksum tool and the flash tool agree on what happened.

OpenRemap fills that gap. It does not try to replace calibration software. It works alongside it.

---

## What OpenRemap actually does

At its core, OpenRemap is a **binary analysis and diff/patch pipeline** built specifically for ECU firmware files:

- **Identifies** ECU binaries — manufacturer, family, software version, hardware number, calibration ID — with a confidence score and the evidence behind it.
- **Verifies checksums** — every known family scheme is detected and reported OK/STALE (detection only, **no correction**).
- **Health-checks** a file — checksums, axis sanity, map counts, erased blocks, VIN duplication — in one command.
- **Diffs** a stock and a tuned binary into a portable **recipe** (`.remap`)
  — every byte-level change, plus (schema 4.4) which calibration maps each
  change touches.
- **Applies** the recipe to a target binary with validate-before → apply → verify-after phases.
- **Audits** — the receipt check: do stock, tuned, and recipe belong together?

OpenRemap **does not flash ECUs** and **does not correct checksums**. It hands you files and verdicts; flashing stays with your dedicated tool.

→ How the pieces fit together: [How it works](../concepts/how-it-works.md)

---

## Why open source

Professional calibration tools are closed systems. That is not a criticism — they carry years of reverse-engineered knowledge, proprietary map definitions, and hardware integration that justify the cost. For a workshop doing this at scale, they are the right choice.

But closed toolchains have a side effect: the knowledge stays inside them. How does the tool know where the maps are? How does it detect a Bosch EDC17 vs an EDC16? What exactly changed between the stock file and the tuned one? Those questions have answers, but the answers are locked away.

OpenRemap is built on the belief that this knowledge should be open, documented, and inspectable. Concretely, that means:

- **The extraction logic is readable.** You can open any extractor and see exactly how it identifies an EDC17, what byte patterns it looks for, and how it builds the match key. If it is wrong, you can fix it.
- **The recipe is inspectable.** Every change is recorded as plain JSON. There are no proprietary formats, no opaque blobs. You can read a recipe in a text editor and understand exactly what it will do before you run it.
- **The pipeline is scriptable.** Every step has a CLI interface with JSON output. You can integrate OpenRemap into your own tools, scripts, or workflows without asking anyone's permission.
- **The community can extend it.** Every ECU family that gets added benefits everyone. A tuner who figures out the SW version pattern for a Siemens SID can contribute an extractor and make the tool work for that entire family permanently.

---

## Project aims

OpenRemap is a **research and educational project.**

The goal is to build open, well-documented tooling for understanding ECU binary structure and the mechanics of binary-level calibration changes — not to enable unsafe or illegal modifications.

Concretely, the project aims to:

- Provide a transparent, auditable alternative for binary diff and patch workflows that is not locked to any commercial tool or vendor
- Build readable, documented extractors that identify ECU families from patterns observable through independent analysis of binaries — without relying on proprietary documentation, Damos files, or any information covered by NDA
- Give tuners and developers a shared vocabulary and toolchain for discussing and working with calibration changes
- Serve as a foundation for further research into ECU binary analysis, calibration portability, and safe patching practices

**What this project is not:**

OpenRemap is not a tool for bypassing emissions systems, deleting DPF or EGR, circumventing speed limiters, or making any modification that is illegal under the laws of your jurisdiction. Pull requests implementing such functionality will not be accepted. Users are solely responsible for ensuring their use of this software complies with applicable laws and regulations.

Any output produced by OpenRemap — recipes, patched binaries, identification results — is for research and analysis purposes. Flashing modified firmware to a vehicle must be done by a qualified professional who has reviewed and validated the calibration for the specific engine, vehicle, and use case.

---

## Frequently asked questions

**Do I need to know how to code to use OpenRemap?**
No. The CLI is designed to be usable by anyone comfortable with a terminal. You run commands, read the output, and pass files around. No programming required.

**Will running `identify` or `cook` modify my files?**
No. Both commands are completely read-only. `identify` reads the binary and prints results. `cook` reads two binaries and writes a recipe JSON — it never touches the input files. The only command that produces a modified binary is `tune`, and even then the original file is never overwritten — the tuned result is written to a separate output file.

**Can I break an ECU just by using OpenRemap?**
Not by identifying, health-checking, or cooking. Patching produces a modified binary, but that binary only matters when you flash it. OpenRemap does not flash anything — it hands you a file. What happens next is your responsibility and your flash tool's job.

**Can I use this on encrypted or scrambled ECU binaries?**
No. OpenRemap works on plaintext binaries where the calibration data is readable. Some ECU variants store the calibration in an encrypted or scrambled region — the extractors will either fail to identify them or return incomplete results. A scrambled EDC16C8, for example, will be identified correctly (the boot sector is not scrambled) but the software version will come back as `null`.

**Does OpenRemap work on files larger than a full ECU flash?**
It works on whatever bytes you give it. If you pass a partial dump or a file with extra padding, the results depend on whether the extractor patterns fall within the data. For best results, use complete, unmodified flash dumps.

---

## Learn more

- [How it works](../concepts/how-it-works.md) — the full pipeline, step by step
- [Getting started](index.md) — install, quick start, command map
- [Recipe format spec](../concepts/recipe-format.md) — the full `.remap` file specification
- [Confidence scoring](../concepts/confidence.md) — how identification confidence works
- [Contributing](../../CONTRIBUTING.md) — how to add a new ECU extractor
- [Disclaimer](../../DISCLAIMER.md) — legal, safety, and intended use
