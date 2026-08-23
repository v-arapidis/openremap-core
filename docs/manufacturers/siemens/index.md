---
title: Siemens
description: Supported Siemens ECU families — Simtec 56 through EMS2000, plus the MS43/GS20/SMG2 checksum coverage.
---

# Siemens

Six extractor families plus the MS43/GS20/SMG2 **checksum-only** coverage
(no extractors — the TCUs and the MS43 boot platform are verified, not
identified).

| Family | Era | File sizes | Vehicles & applications | Page |
|---|---|---|---|---|
| **Simtec 56** | 1995–2002 | 128 KB | Opel/Vauxhall Vectra B, Astra, Omega B, Calibra | [simtec56](simtec56.md) |
| **SIMOS** | 1998–2006 | 131–524 KB | VW/Audi/Skoda/Seat 1.4–1.6L petrol | [simos](simos.md) |
| **PPD1.x** | 2003–2008 | 250 KB–2 MB | VAG 2.0 TDI Pumpe-Düse diesel | [ppd](ppd.md) |
| **SID 801 / SID 801A** | 2001–2006 | 512 KB | Peugeot/Citroën 2.0/2.2 HDi diesel | [sid801](sid801.md) |
| **SID 803 / SID 803A** | 2005–2010 | 458 KB–2 MB | Peugeot/Citroën, Ford, Jaguar/Land Rover diesel | [sid803](sid803.md) |
| **EMS2000** | 1996–2004 | 256 KB | Volvo S40/V40/S60/S70/V70 T4/T5 turbo petrol | [ems2000](ems2000.md) |
| **MS43** (checksum-only) | 2001–2006 | 512 KB | BMW E46 6-cylinder petrol | [ms43](ms43.md) |

Checksum profiles also cover the Siemens **GS20 / SMG2 TCUs**
(CRC-16/ARC). See [Checksums command docs](../../commands/checksum/advanced.md).

→ [Siemens internals](internals.md) — detection strategies and ident formats