# Third-party credits & community ground truth

OpenRemap's checksum and detection knowledge is built on open community
work. We port **algorithms** (never copy code wholesale) into an MIT
codebase; these projects deserve the credit.

## Community projects (algorithm sources)

| Project | What we took | License | Where it lands |
|---|---|---|---|
| [ME7Sum](https://github.com/b-stefanski/ME7Sum) | Bosch ME7 main + multipoint checksum scheme | GPL | `openremap/core/services/checksums/checksum.py`, Rust sweep |
| [IronFelix](https://github.com/weichengl/ironfelix) | ME7.XX / M3.x / M7.9.7 / M7.9.8 / ME7.4.5 / Samand checksum profiles | GPL | `openremap/core/services/checksums/ironfelix.py` |
| [NefMoto Open Source](https://nefmoto.com/) | ME7 rolling / multirange checksum detection (firmware-code-driven) | GPL / community | `openremap/core/services/checksums/nefmoto.py` |
| [RomRaider](https://github.com/RomRaider/RomRaider) | `RomChecksum.java` descriptor-table algorithm (basis of the Denso Subaru scheme) | GPL v2 | `openremap/core/services/checksums/denso.py` |
| [MS4X Wiki](https://www.ms4x.net) | GS20 / SMG2 TCU checksum corrector algorithm (decompiled community tool) | community | `openremap/core/services/checksums/ironfelix.py` (GS20/SMG2) |
| [td-d/SubaruDefs](https://github.com/td-d/SubaruDefs) | EcuFlash Subaru defs — checksum table addresses and layout knowledge | community | `openremap/core/services/checksums/denso.py` |
| [bludgod/RomRaider](https://github.com/bludgod/RomRaider) | The 501-file Subaru factory-ROM corpus | community | `tests/data/ECUs/Subaru/` (gitignored) |

## Used as libraries (runtime dependencies)

| Project | What we use | License | Where it lands |
|---|---|---|---|
| [orjson](https://github.com/ijl/orjson) | Fast, spec-strict JSON parsing of recipe files | Apache-2.0 / MIT | recipe-load paths (tune/validate/merge/audit/diff-maps/TUI) |
| [bincopy](https://github.com/eerimoq/bincopy) | Intel HEX / Motorola S-Record parsing + record checksum validation | MIT | `openremap/core/services/convert.py`, `openremap convert` |
| [vininfo](https://github.com/idlesign/vininfo) | VIN decoding — WMI → manufacturer/region/country, model years, ISO 3779 check digit | BSD-3-Clause | `openremap/core/services/vin_decode.py` (scan-vins/health/identify) |
| [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) | Fuzzy string matching for family-name suggestions | MIT | `families --family` fuzzy lookup |

## What "port" means here

- We implement the **algorithm** from the documented behaviour and
  validate against real binaries — we do not copy implementation code.
- Where semantics were ambiguous (e.g. the Denso end-inclusive `end`
  address), the factory corpus resolved it; deviations are documented in
  the module docstrings.
- The Rust ports are 1:1 re-implementations of our own validated Python,
  not copies of the upstream C/Java.

## Sources of structural knowledge (non-code)

- EcuFlash / OpenECU definitions (via td-d/SubaruDefs and the
  SubaruDefs mirror) — ROM layouts, flash methods.
- The ECU tuning community's public forum knowledge (calibration ID
  conventions, flash layout facts) — credited inline where relied upon.
