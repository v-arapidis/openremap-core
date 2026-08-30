# OpenRemap

🌐 **[openremap.com](https://www.openremap.com)** — the project site: what OpenRemap is, how to install it, and the latest news.

📚 **[docs.openremap.com](https://docs.openremap.com)** — the wiki: concepts, every command, and per-family references.

🐙 **[openremap-docs](https://github.com/v-arapidis/openremap-docs)** — the open-source repo behind the docs/wiki — suggestions and contributions welcome.

[![CI](https://github.com/v-arapidis/openremap-core/actions/workflows/ci.yml/badge.svg)](https://github.com/v-arapidis/openremap-core/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/openremap.svg)](https://pypi.org/project/openremap/)
[![Changelog](https://img.shields.io/badge/-Changelog-blue.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

**The open-source ECU binary intelligence layer** — identify firmware,
health-check it, verify its checksums, diff a tune into a portable recipe,
and apply it to another binary of the same family.

Offline-first. No accounts, no telemetry, no data leaves your machine.

OpenRemap began as a final-semester thesis project, presented at
**SAEK Orestiadas** (v0.4.5), and has been actively developed since
(current release line: 0.7.x).

---

## Where the project is going (short-term roadmap)

**0.7.x (current):** stabilise the 0.7.0 release — fix bugs that surface
in real use, and integrate third-party open-source libraries (ported
with credit — never copy-paste, see [THIRD_PARTY.md](THIRD_PARTY.md)).
This is a **stability** cycle, not a feature cycle.

**Upcoming milestones:**

- **0.8.0** — cross-firmware experiment: learn a tune from one software
  revision and relocate it to another by structural map matching, plus
  community plugin tooling.
- **0.9.0** — a refined, modern TUI.
- **1.0.0** — **OpenRemap Harness**: a desktop app for Windows / macOS /
  Linux.

---

## Quick start

```bash
pip install openremap        # or: uv tool install openremap

openremap identify stock.bin     # what is this ECU?  (family, SW, confidence)
openremap health  stock.bin      # is the file sane?  (checksums, maps, VINs)
openremap cook    stock.bin tuned.bin --output stage1.remap   # diff → recipe
openremap cook-volatile stockA.bin stage1.bin --output portable.remap  # car-portable recipe
openremap tune    target.bin stage1.remap --output tuned.bin  # apply → verify
```

Runs entirely offline. `.bin`, `.ori`, and `.hex` files are all accepted.

## What it is

- **Identify** — 36 extractors across 6 OEMs (Bosch, Siemens, Delphi,
  Magneti Marelli, Denso, Hitachi), each result with a confidence tier
  and the evidence behind it
- **Health-check** — one command: checksums, axis sanity, map-count
  envelope, erased blocks, VIN duplication (CI-gateable)
- **Checksums** — verifies ME7, IronFelix, NefMoto, MS43, GS20/SMG2 and
  Denso Subaru schemes (detection only, no correction)
- **Cook / tune** — diff stock vs tuned into a portable `.remap` recipe
  (schema 4.5, map-annotated; `cook-volatile` excludes VIN/checksum-store
  bytes with evidence for cross-car portability), apply with
  validate-before → apply → verify-after; merge recipes; audit the receipt
- **Map tooling** — structural map discovery, map-level diffing, CSV
  export, probabilistic labels
- **Library-first** — every service is importable Python (identity,
  patching, map scanning) with a mandatory Rust core for the hot loops

## Supported ECUs

6 manufacturers, 36 extractors — from LH-Jetronic (1982) to EDC17 and
Denso/Hitachi Subaru (2020s). → [Per-family reference](https://docs.openremap.com/manufacturers/)

## Decoders & CPU coverage

To extract code references and read routines, OpenRemap decodes the ECU's
CPU directly — no manufacturer lookup needed. Decoders power the
code-reference signal, CPU auto-detection, and the `routine` command's
pseudo-decompiler. Two backends cover the supported families:

- **Rust-native decoders** — written from scratch for CPUs that the capstone
  disassembly library does not support (C166/ST10, 8051, MCS-96), each
  verified against an independent oracle: Ghidra's SLEIGH spec (C166), the
  `at51` disassembler (8051 — 100% agreement on ~312k real instruction
  boundaries), and MAME's opcode tables + Ghidra (MCS-96).
- **Capstone-backed decoders** — the mature disassembly library, used for
  TriCore, SuperH, x86, M680X, 68K and PowerPC.

| CPU | Decoder | ECU families |
|---|---|---|
| C166 / ST10 | Rust | ME7, ME9, EDC15, MS43, PPD, SID801/803, EMS2000, M5.x, ME1.5.5 |
| 8051 (MCS-51) | Rust | M1.8, M2.x, MP9, M4.x, Mono-Motronic, SIMOS, Simtec56 |
| MCS-96 (8096) | Rust | EDC1, EDC3 |
| TriCore | capstone | EDC16, EDC17, MED9, MED17 |
| SuperH | capstone | SH7055, SH7058, SH72531, SH72546 |
| M680X (68HC11 / 6800) | capstone | M1.3, M1.7, M3.x, MP3.x, MP7.2, LH-Jetronic |
| 68K (68000 / CPU32) | capstone | M1.5.5, M1.55, IAW 4LV |
| PowerPC | capstone | MJD 6JF |
| x86 | capstone | (generic code — bootloaders etc.) |

Every mapping above is evidence-backed: an internal audit established each
family's CPU from reset-vector header bytes and independent decoder
evidence, and corrected several extractor docstrings along the way (M2.x /
MP9 / M4.x are 8051, not 68xxx; EDC16 / MED9 are TriCore, not C166). Denso
EE20 is believed to be SuperH per community docs, but there is no corpus
binary to verify against yet.

## Documentation

The wiki is live at **[docs.openremap.com](https://docs.openremap.com)** —
its content lives in the open-source
[`v-arapidis/openremap-docs`](https://github.com/v-arapidis/openremap-docs)
repo.  This repository keeps only repo-internal docs
([`docs/internal/`](docs/internal/) — audits, roadmaps).

- [Getting started](https://docs.openremap.com/getting-started/) — install, quick start, CLI, TUI
- [Concepts](https://docs.openremap.com/concepts/) — how it works, decoders, confidence, evidence, recipe format
- [Commands](https://docs.openremap.com/commands/) — every command, simple + advanced
- [Manufacturers](https://docs.openremap.com/manufacturers/) — per-OEM, per-family pages

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — extractor guides, code style, PR process.
Changes are tracked per version in [`changelog/`](changelog/).

## License

[MIT](LICENSE) · [Third-party credits](THIRD_PARTY.md)
