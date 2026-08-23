# OpenRemap

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

🌐 **[openremap.com](https://www.openremap.com)** — full documentation, wiki, and changelog.

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

- **Identify** — 35 extractors across 6 OEMs (Bosch, Siemens, Delphi,
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

6 manufacturers, 35 extractors — from LH-Jetronic (1982) to EDC17 and
Denso/Hitachi Subaru (2020s). → [Per-family reference](docs/manufacturers/)

## Documentation

The [wiki](docs/README.md) is organised by domain:

- [Getting started](docs/getting-started/index.md) — install, quick start, CLI, TUI
- [Concepts](docs/concepts/index.md) — how it works, confidence, evidence, recipe format
- [Commands](docs/commands/index.md) — every command, simple + advanced
- [Manufacturers](docs/manufacturers/index.md) — per-OEM, per-family pages

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — extractor guides, code style, PR process.
Changes are tracked per version in [`changelog/`](changelog/).

## License

[MIT](LICENSE) · [Third-party credits](THIRD_PARTY.md)
