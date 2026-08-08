# OpenRemap Documentation

Welcome to the OpenRemap docs. Use the links below to navigate.

---

## Getting started

| Document | Description |
|---|---|
| [About OpenRemap](about.md) | What the project does, how it works, use cases, FAQ |
| [Installation](setup.md) | Setup guide with links to platform-specific instructions |
| [Quick start](quickstart.md) | 5-minute getting started guide — identify, scan, cook, tune |
| [Interactive TUI](tui.md) | The graphical terminal interface — panels, shortcuts, file dialogs |
| [CLI reference](cli.md) | All commands at a glance with examples |

---

## Commands

| Command | Description |
|---|---|
| [`openremap workflow`](commands/workflow.md) | Step-by-step guide — start here if you are new |
| [`openremap commands`](commands/commands.md) | One-line cheat-sheet of all commands |
| [`openremap families`](commands/families.md) | List every supported ECU family |
| [`openremap scan`](commands/scan.md) | Batch-scan and sort a folder of ECU files |
| [`openremap identify`](commands/identify.md) | Read a binary and print everything extracted |
| [`openremap cook`](commands/cook.md) | Diff stock vs. tuned → save as a `.remap` recipe |
| [`openremap tune`](commands/tune.md) | One-shot: validate → apply → verify |
| [`openremap validate`](commands/validate.md) | Individual validation steps (before, check, after) |

---

## Concepts

| Document | Description |
|---|---|
| [Architecture overview](architecture.md) | How the pieces connect — entry points, service layer, extractor registry |
| [Confidence scoring](confidence.md) | How identification confidence tiers, signals, and warnings work |
| [Recipe format](recipe-format.md) | The `.remap` file spec — fields, structure, anchor search |
| [Tune format](orst-format.md) | The `.orst` file spec — workspace-native saved tune |

---

## Supported manufacturers

| Manufacturer | Families | Internals |
|---|---|---|
| [Bosch](manufacturers/bosch.md) | 19 families — EDC1 through MD1 | [Bosch internals](manufacturers/bosch-internals.md) |
| [Siemens](manufacturers/siemens.md) | 6 families — Simtec 56 through EMS2000 | [Siemens internals](manufacturers/siemens-internals.md) |
| [Delphi](manufacturers/delphi.md) | 2 families — Multec, Multec S | |
| [Magneti Marelli](manufacturers/marelli.md) | 4 families — IAW 1AV/1AP/4LV, MJD 6JF | |

---

## Project

| Document | Description |
|---|---|
| [Contributing](../CONTRIBUTING.md) | How to add an extractor, code style, PR process |
| [Changelog](../CHANGELOG.md) | Version history |
| [Disclaimer](../DISCLAIMER.md) | Legal, safety, and intended use |
| [License](../LICENSE) | MIT License |

---

← [Back to project README](../README.md)