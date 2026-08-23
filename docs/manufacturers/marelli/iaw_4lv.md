---
title: IAW 4LV
description: VAG multi-point petrol, M68K
---

# IAW 4LV

| | |
|---|---|
| Era | 2000s |
| File sizes | 512 KB |
| CPU | Motorola 68332/68336 (M68K) |
| Vehicles | VAG — Skoda Fabia 1.4 16V 100HP, VW, Seat |

## Detection

Unique size + header magic (0x0E00E683) + byte-swapped ASCII strings (`AMERLL` = MARELLI) + 55AA33CC footer markers.

**Checksums:** no family profile (generic sweep only).

→ [Back to Marelli](index.md)