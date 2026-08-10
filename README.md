# OpenRemap

[![CI](https://github.com/v-arapidis/openremap-core/actions/workflows/ci.yml/badge.svg)](https://github.com/v-arapidis/openremap-core/actions/workflows/ci.yml)

[![PyPI](https://img.shields.io/pypi/v/openremap.svg)](https://pypi.org/project/openremap/)
[![Changelog](https://img.shields.io/badge/-Changelog-blue.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

**An offline-first engine for analyzing, comparing, and reproducing ECU binary changes.**

Embed it as a library, automate it in a pipeline, or run it from the terminal — your ECU data never leaves your machine.

🌐 **[openremap.com](https://www.openremap.com)** — full documentation, guides, and changelog.

> Runs entirely offline. No accounts, no telemetry, no data leaves your machine — ever.

---

## What it is

OpenRemap is an **offline-first ECU binary analysis engine**, exposed as a Python library and a CLI. The same pipeline that powers the command-line tool is fully available as importable services — no subprocess, no parsing stdout.

```python
from openremap.core.services.identifier import identify_ecu
from openremap.core.services.confidence import score_identity
from openremap.core.services.patcher    import ECUPatcher

data     = open("ecu.bin", "rb").read()
identity = identify_ecu(data, filename="ecu.bin")
result   = score_identity(identity)

print(f"{identity['ecu_family']}  {result.tier}")  # EDC17  High
```

All services accept `bytes` and `dict` — no file paths, no hidden state, trivial to test and wrap in an API endpoint or a desktop app.

CPU-bound algorithms (entropy analysis, context-anchor search) run on a compiled Rust backend via PyO3. Pre-built wheels ship the native extension for Linux, macOS, and Windows — `pip install openremap` and it just works. A pure-Python fallback covers platforms without pre-built wheels.

→ [Full integration guide](docs/integration.md)

---

## The problem

ECU work revolves around three tasks that today require expensive commercial software (€2,500–€8,000+) or manual hex-editor work with no audit trail:

1. **Identify a binary** — manufacturer, ECU family, software revision, confidence score
2. **Diff two binaries** — what changed between a stock file and a tuned file, captured as a portable recipe
3. **Apply a recipe to another binary** — validate, patch, and verify — all-or-nothing

Whether you are building a tuning application, automating a workflow, or just need a reliable offline tool — OpenRemap gives you a clean Python API and a full CLI for all three.

## Features

- **ECU identification** — 30 extractors across Bosch, Siemens, Delphi, and
  Magneti Marelli. Every binary gets back manufacturer, family, software
  version, hardware number, and a multi-signal confidence score.
- **Batch scanning** — point it at a folder, get every file classified and
  optionally sorted into manufacturer/family trees. Suspicious files are
  flagged before you touch them.
- **Structural map discovery** — find calibration axes and 2D tables in any
  binary without manufacturer identification. Works on unsupported ECUs.
- **Portable recipes** — diff two binaries into a `.remap` JSON recipe. Every
  changed byte is captured with a 32-byte context anchor. Human-readable,
  Git-diffable, shareable.
- **Safe patching** — validate, apply, verify — all in one shot. All-or-nothing:
  partial patches never happen.
- **Terminal UI** — full interactive interface for identifying, scanning,
  cooking, and tuning. Run `openremap` with no arguments.
- **Python library** — every service is importable directly. No subprocess,
  no parsing stdout. Embed in scripts, pipelines, or desktop apps.
- **Rust acceleration** — CPU-bound algorithms run on a compiled native
  extension. Pre-built wheels for Linux, macOS, and Windows. Pure-Python
  fallback included.

→ [CLI reference](docs/cli.md) · [Integration guide](docs/integration.md)

### What it does NOT do

- **Map editing** — OpenRemap works at the byte level, not the map level. Use WinOLS or ECM Titanium to find and edit maps. Use OpenRemap to capture, share, and reapply those edits.
- **Checksum correction** — you must run the output through WinOLS, ECM Titanium, or equivalent before flashing. Always.
- **ECU reading/writing** — it operates on `.bin` files you already have.

---

## Supported ECUs

30 extractors across 4 manufacturers, covering ECUs from 1982 to present:

| Manufacturer | Families | Examples |
|---|---|---|
| **Bosch** (18) | EDC17, EDC16, EDC15, ME7, ME9, M5.x, M4.x, M3.x, M2.x, M1.x, MP9, ME1.5.5, LH-Jetronic, Mono-Motronic, and more | VAG TDI, BMW, Volvo, PSA, Porsche, Alfa Romeo |
| **Siemens** (6) | SIMOS, PPD, SID 801/803, Simtec 56, EMS2000 | VAG petrol, PSA/Ford diesel, Volvo turbo |
| **Delphi** (2) | Multec, Multec S | Opel/Vauxhall diesel and petrol |
| **Marelli** (4) | IAW 1AV, IAW 1AP, IAW 4LV, MJD 6JF | Fiat, PSA, GM/Opel |

→ Full reference: [Bosch](docs/manufacturers/bosch.md) · [Siemens](docs/manufacturers/siemens.md) · [Delphi](docs/manufacturers/delphi.md) · [Marelli](docs/manufacturers/marelli.md)

---

## Confidence scoring

Every identification includes a confidence verdict — `HIGH`, `MEDIUM`, `LOW`, `SUSPICIOUS`, or `UNKNOWN` — built from multiple signals:

- **Detection strength** — how rigorous the extractor's matching cascade is
- **Software version format** — manufacturer-aware canonical format checking (Bosch `1037`-prefixed, Delphi 8-digit GM-style, etc.)
- **Identity fields present** — hardware number, calibration ID, ECU variant
- **Filename analysis** — tuning keywords (`stage2`, `dpf_off`, `egr_off`) and generic names (`1.bin`) flag suspicious files
- **Family-aware scoring** — ECU families that architecturally lack certain fields are never penalised for their absence

→ [Full scoring breakdown](docs/confidence.md)

---

## The recipe format

The `.remap` recipe is a self-contained JSON file. Every changed byte is listed with its offset, original value, modified value, and a context anchor — 32 bytes of surrounding data that let the patcher find the right location even if the binary has shifted slightly between software revisions.

Recipes are human-readable, Git-diffable, and shareable. No proprietary format, no binary blobs. A recipe is a portable, reproducible record of a tune — you can review it, version it, and apply it to any matching ECU.

→ [Recipe format specification](docs/recipe-format.md)

---

## Install

Works on Windows, macOS, and Linux. One command:

```bash
pip install openremap
```

Or with [uv](https://github.com/astral-sh/uv) (recommended):

```bash
uv tool install openremap
```

Pre-built wheels include the compiled Rust backend — no Rust toolchain required. On platforms without a pre-built wheel, the pure-Python backend takes over automatically.

Detailed guides:

- 🪟 **Windows** — [Step-by-step guide](docs/install/windows.md) · written for people who rarely use a terminal
- 🍎 **macOS / 🐧 Linux** — [One-command install](docs/install/macos-linux.md)
- 🛠️ **Contributing / development** — [Clone and run from source](docs/install/developers.md)

---

## Get started

```bash
openremap
```

The full terminal UI launches — identify files, scan folders, cook recipes, and apply tunes, all from one interface.

The complete CLI is also there when you need it:

```bash
openremap workflow    # Prints a plain-English step-by-step guide
openremap commands    # Quick reference of all available commands
```

→ [Full CLI reference](docs/cli.md)

---

## Documentation

- 🌐 [openremap.com](https://www.openremap.com) — website with full guides and changelog
- [Integration guide — using OpenRemap as a Python library](docs/integration.md)
- [How it all works](docs/about.md)
- [CLI commands overview](docs/cli.md)
- [Confidence scoring — tiers, signals, and breakdown](docs/confidence.md)
- [Recipe format (.remap)](docs/recipe-format.md)
- Supported families: [Bosch](docs/manufacturers/bosch.md) · [Siemens](docs/manufacturers/siemens.md) · [Delphi](docs/manufacturers/delphi.md) · [Marelli](docs/manufacturers/marelli.md)
- [Contributing — adding extractors, code style, PRs](CONTRIBUTING.md)

---

## Contributing

Contributions are welcome — especially new ECU family extractors. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

<p align="center">
  <img src="docs/images/tui-scan.png" alt="OpenRemap TUI — Scan panel" width="820">
</p>

---

> ⚠️ **Checksum verification is mandatory.** Before flashing any tuned binary to a vehicle, you **must** run it through a dedicated checksum correction tool (ECM Titanium, WinOLS, or equivalent). `openremap tune` confirms the recipe was applied correctly — it does **not** correct or validate ECU checksums. Flashing a binary with an incorrect checksum **will brick your ECU.**

> ⚠️ **Research and educational use only.** Any output produced by this software must be reviewed by a qualified professional before being flashed to a vehicle. The authors accept no liability for damage, loss, or legal consequences arising from its use. Read the full [DISCLAIMER](DISCLAIMER.md).
