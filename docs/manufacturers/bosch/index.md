---
title: Bosch
description: Supported Bosch ECU families — 18 extractors, 1982 to the present.
---

# Bosch

Bosch is the dominant ECU maker in this registry: **18 extractors** spanning
1982 to the present — from LH-Jetronic fuel injection to the EDC17 family
that powers most modern diesels.

## Family reference

| Family | Era | File sizes | Vehicles & applications | Page |
|---|---|---|---|---|
| **EDC1 / EDC2** | 1990–1997 | 32–64 KB | Audi 80/A6 TDI, early common-rail diesel | [edc1](edc1.md) |
| **EDC 3.x** | 1993–2000 | 128–512 KB | VAG TDI, BMW diesel, Opel diesel | [edc3x](edc3x.md) |
| **EDC15** | 1997–2004 | 512 KB | VAG, Fiat, Volvo, BMW diesel | [edc15](edc15.md) |
| **EDC16** | 2003–2008 | 256 KB–2 MB | VAG PD/CR TDI, BMW diesel, Opel/GM diesel | [edc16](edc16.md) |
| **EDC17 / MEDC17 / MED17 / ME17 / MD1** | 2008–present | 2–8 MB | VAG, BMW, Mercedes, PSA diesel and petrol | [edc17](edc17.md) |
| **MED9** | 2002–2008 | 512 KB–2 MB | VAG FSI/TFSI petrol direct injection | [med9](med9.md) |
| **ME7** | 1997–2008 | 64 KB–1 MB | VAG 1.8T, Porsche, Ferrari, Opel | [me7](me7.md) |
| **ME9** | 2001–2006 | 2 MB | VW/Audi 1.8T 20v full flash | [me9](me9.md) |
| **M1.x / M1.55 / M1.5.5** | 1987–2002 | 32–128 KB | BMW E28–E36, Opel, Alfa Romeo petrol | [m1x](m1x.md) |
| **M2.x** | 1993–1999 | 32–128 KB | VW/Audi, Porsche 964, Opel | [m2x](m2x.md) |
| **M3.x / MP3.x / MP7.x** | 1989–1999 | 32–256 KB | BMW E30/E36, PSA/Citroën petrol | [m3x](m3x.md) |
| **M4.x** | 1994–2002 | 64–128 KB | Volvo 850/960/S70/V70/S80 petrol | [m4x](m4x.md) |
| **M5.x / M3.8x** | 1997–2004 | 128–256 KB | VW/Audi 1.8T (AGU, AUM, APX) | [m5x](m5x.md) |
| **ME1.5.5** | 1998–2004 | 128–256 KB | Alfa Romeo, Fiat, Opel petrol | [me155](me155.md) |
| **MP9** | 1996–2002 | 64 KB | VW/Seat/Skoda 1.0–1.6L petrol | [mp9](mp9.md) |
| **Mono-Motronic** | 1989–1997 | 32–64 KB | VW/Audi/Seat single-point injection | [mono](mono.md) |
| **LH-Jetronic** | 1982–1995 | 8–64 KB | Volvo, Porsche 928, BMW, Mercedes | [lh](lh.md) |
| **Motronic Legacy** | various | 2–32 KB | Porsche DME-3.2, KE-Jetronic, EZK | [motronic-legacy](motronic-legacy.md) |

→ [Bosch internals](internals.md) — detection strategies and OEM-specific notes

## Confidence scoring

Every identification result includes a confidence tier. High = strong,
unambiguous identification signals; Low/Suspicious = check before relying
on it. See [Confidence scoring](../../concepts/confidence.md).

## Checksums

Family checksum profiles exist for **ME7** (main/multipoint/rolling/
multirange + IronFelix ME7.XX) and **M3.x** (IronFelix M3.x-5.x). The
generic sweep engine runs on every family. See
[Checksums — support matrix](../../commands/checksum/advanced.md).
