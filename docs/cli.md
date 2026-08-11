# CLI Reference

Full reference for the `openremap` command-line interface. For interactive use,
just run `openremap` with no arguments to launch the TUI.

No server, no database, no internet connection required — install and run anywhere.

> **New to the terminal?** Run `openremap workflow` first. It prints a complete
> plain-English walkthrough with the exact commands to type and what to look for
> at each step. No reading required.
>
> **Know the commands already?** Run `openremap commands` for a one-line-per-command
> cheat-sheet.

---

## Installation

See the full setup guide → [`docs/setup.md`](setup.md)

Quick start for most users — `openremap` is on [PyPI](https://pypi.org/project/openremap/):

```bash
uv tool install openremap
```

Prefer plain pip?

```bash
pip install openremap
```

`openremap` is then available from any folder, no activation required.
Shell completion, development setup, updating, and troubleshooting are all
covered in [`docs/setup.md`](setup.md).

---

## Commands

Every command supports `--help` for a quick reminder of its arguments and options.

| Command | What it does | Reference |
|---|---|---|
| `commands` | Compact cheat-sheet — all commands at a glance | [→ commands.md](commands/commands.md) |
| `workflow` | Step-by-step guide — start here if you are new | [→ workflow.md](commands/workflow.md) |
| `families` | List every supported ECU family with era, size, and notes | [→ families.md](commands/families.md) |
| `families --family <NAME>` | Full detail for one ECU family | [→ families.md](commands/families.md) |
| `scan` | Sort a folder of ECU files by manufacturer and family | [→ scan.md](commands/scan.md) |
| `identify` | Read an ECU binary and print everything extracted from it | [→ identify.md](commands/identify.md) |
| `scan-maps` | Structural scan — find calibration map axes and 2D tables without identification | [→ scan-maps.md](commands/scan-maps.md) |
| `cook` | Compare a stock and a tuned binary and save the difference as a recipe | [→ cook.md](commands/cook.md) |
| `tune` | **One-shot:** validate before → apply → validate after | [→ tune.md](commands/tune.md) |
| `validate before` | Pre-flight check — run before tuning (or use `tune`) | [→ validate.md#before](commands/validate.md#before) |
| `validate check` | Diagnostic — run when `validate before` fails | [→ validate.md#check](commands/validate.md#check) |
| `validate after` | Post-tune confirmation — run after tuning (or use `tune`) | [→ validate.md#after](commands/validate.md#after) |
| `openremap-server` | Start a long-running JSON-RPC server for non-Python integrations | [→ integration.md#11-server-mode](integration.md#11-server-mode---long-running-json-rpc-process) |

> **`validate strict` / `validate exists` / `validate tuned`** are deprecated aliases
> for `validate before` / `validate check` / `validate after`. They still work but
> print a rename notice. Update your scripts when convenient.

---

## Quick-start example

```bash
# New here? Print the full step-by-step guide first
openremap workflow

# Need a quick reminder of all commands?
openremap commands

# Not sure if your ECU is supported?
openremap families
openremap families --family EDC16

# (Optional) Sort a folder of binaries into a tidy library
openremap scan ./my_bins/                    # preview — nothing moves
openremap scan ./my_bins/ --move --organize  # sort into Bosch/EDC17/ etc.

# (Optional) Discover calibration maps in an unsupported or unknown ECU
openremap scan-maps ecu.bin
openremap scan-maps ecu.bin --region 0x10000-0x80000 --min-score 0.85 --show-series

# 1. Read the stock binary — confirm it is a supported ECU
openremap identify stock.bin

# 2. Extract the tune — diff stock vs tuned and save as a recipe
openremap cook stock.bin stage1.bin --output recipe.remap

# 3. One-shot: validate before → apply → validate after
openremap tune target.bin recipe.remap

# If tune fails at Phase 1 — diagnose why
openremap validate check target.bin recipe.remap

# 4. MANDATORY — correct checksums with ECM Titanium, WinOLS, or equivalent
#    before flashing the tuned binary to any vehicle
```

---

## Server mode

`openremap-server` starts a long-running JSON-RPC daemon that non-Python
applications (desktop editors, IDE plugins, automation tools written in Rust,
Go, C++, Electron, etc.) can use to call the full openremap pipeline without
cold-starting a Python interpreter on every request.

```bash
# Start the server — reads requests from stdin, writes responses to stdout
openremap-server

# Equivalent
python -m openremap.server

# Quick smoke-test
echo '{"id":1,"method":"ping","params":{}}' | openremap-server
# → {"id": 1, "result": {"ok": true}}
```

The server stays alive until stdin is closed or the process is terminated.
All eight pipeline operations are available: `ping`, `version`, `identify`,
`cook`, `tune`, `validate`, `scan_maps`, and `scan`.

For the full protocol reference, request/response schemas, and a minimal
client example see **[`docs/integration.md` — Section 11](integration.md#11-server-mode---long-running-json-rpc-process)**.

---

## Other documentation

| Document | Contents |
|---|---|
| [`docs/commands/`](commands/) | Per-command reference — arguments, options, examples, example output |
| [`docs/confidence.md`](confidence.md) | Confidence scoring — tiers, signals, warnings, and score breakdown |
| [`docs/manufacturers/bosch.md`](manufacturers/bosch.md) | Supported Bosch ECU families — ident formats, file sizes, SW/HW layout |
| [`docs/manufacturers/siemens.md`](manufacturers/siemens.md) | Supported Siemens ECU families |
| [`docs/manufacturers/delphi.md`](manufacturers/delphi.md) | Supported Delphi ECU families |
| [`docs/manufacturers/marelli.md`](manufacturers/marelli.md) | Supported Magneti Marelli ECU families |
| [`docs/about.md`](about.md) | How it works — the recipe format, the match key, use cases, FAQ |
| [`docs/recipe-format.md`](recipe-format.md) | The recipe format spec (.remap) — fields, structure, versioning |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | How to add a new ECU extractor, code style, submitting a PR |
| [`DISCLAIMER.md`](../DISCLAIMER.md) | Liability, intended use, professional review requirements |