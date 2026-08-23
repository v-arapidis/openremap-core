---
title: checksum — advanced
description: Verify known checksum schemes — every flag, output fields, examples.
---

# `openremap checksum`

Detect which checksum schemes an ECU binary satisfies — and whether the
known family schemes are **OK** or **STALE**. Detection only; the command
never corrects anything.

Two layers:

1. **Sweep** — a closed config space (11 algorithm families × init values
   × final XOR × regions × store locations × direct/complement forms) at
   native speed. A per-page scheme matching ≥ 90% of non-erased pages is a
   strong signal; a single whole-file match is weak evidence.
2. **Known family schemes** — Bosch ME7 (main + multipoint + rolling +
   multirange) and the IronFelix family profiles (VAG ME7.XX subtypes,
   M3.x, Hyundai/China M7.9.x, Citroen ME7.4.5, Sagem), plus the
   Siemens GS20/SMG2 TCU profiles — ported from the open-source
   community projects ME7Sum, IronFelix, NefMoto and the MS4X wiki.

> Most freely-downloaded dumps carry stale or stripped checksums and yield
> no matches. OK/STALE verdicts on the family profiles are the trustworthy
> signal (see the ISSUE-3 checksum-discovery status).

---

## Usage

```bash
openremap checksum <FILE> [--json]
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--json` | off | Output as JSON instead of a table. |

---

## Example

```
  OpenRemap — Checksum Detection
  Audi S4 4.2l ME7.1.1  •  1,048,576 bytes  •  0 scheme(s) detected

  Bosch ME7 main checksum: OK  (stored E090F65E, expected E090F65E)
  Bosch ME7 multipoint: 18 block(s) verify, 8 bootrom descriptor(s) not verifiable
  Bosch ME7 rolling: 3/3 slot(s) verify

  IronFelix family profiles
  ──────────────────────────────────────────────────────────────────────────
  VAG Bosch ME7.XX (subtype 6)       3/3 checks ok  multipoint 18 ok / 49 unverifiable
```

ME7 firmware may use three different checksum types on top of the main
sum (some files use all of them):

| Type | What it is |
|---|---|
| main | u32 sum of LE u16 words over descriptor blocks, stored (v, ~v) @ file_end−0x20 |
| multipoint | per-block 16-byte descriptors (start, end, v, ~v) |
| rolling | seed-table hash (init 0xFFFFFFFF, inverted) over byte ranges — detected from the firmware's own checksum code |
| multirange | u32 byte-sum (v, ~v) over ranges — detected from the firmware code |

The rolling/multirange structures are located by pattern-matching the
C166 machine code inside the firmware itself (NefMoto port), so they
work across firmware variants without a family database.

`subtype` is the IronFelix ME7.XX sub-classification (1/2/3/5/6); different
subtypes use different CRC-32/sum8 zone layouts. Every family check is
reported as `ok`, `stale` (stored value differs from the recomputed one),
or `absent` (structure missing in this file).

---

## Family profiles

| Family | Target | Checks |
|---|---|---|
| `vag_me7xx` | VAG Bosch ME7.XX, 512 KB / 1 MB | subtype CRC-32 zones (1/2/3/5) or sum8 zones (5/6), 0x803C block, main tail, multipoint |
| `me3x` | Bosch M3.x-5.x, 128/256 KB | sum8 from page-marker table, BE16 triplets |
| `m797` | Hyundai Bosch M7.9.7, 512 KB | 5-zone sum16le main tail + multipoint |
| `m798` | Hyundai Bosch M7.9.8, 768/832 KB | 3 fixed zone sums, signature block sum, multipoint |
| `china797` | China Bosch M7.9.7, 1 MB | 2-zone main tail + multipoint |
| `me745` | Citroen Bosch ME7.4.5, 832 KB | page-block sum, multipoint, 3× CRC-32 |
| `samand` | Sagem Iran Khodro, 832 KB | 2× sum8 stored LE16 |
| `gs20` | Siemens GS20 TCU, 64 KB data / 256 KB program | CRC-16/ARC over fixed ranges, stored LE16 |
| `smg2` | Siemens SMG II TCU, 32 KB | CRC-16/ARC init 0x7878 over [8416, 30911] |
| `ms43` | Siemens MS43 ECU (BMW M54), 512 KB | 3× CRC-16/ARC over descriptor blocks — boot @0x3C24, program @0x6FDE0, calibration @0x73FE0; init words read from the firmware's ID strings; the two 32-bit monitor sums are runtime checks and are reported (not verified) |

The `vag_me7xx` and `me3x` profiles are validated against the real corpus
in `tests/data/ECUs/` (ME7.1: 116/137 files fully OK; M3.8: 18/20); `gs20`
is validated on the real GS20 factory + tuned pairs; the others are
validated on synthetic fixtures (no corpus files yet).

> **GS20 corpus quirk:** one "corrected" tuned file in the corpus is
> STALE — its author wrote the CRC value byte-swapped (0x190B as `19 0B`
> instead of LE `0B 19`). The detector correctly reports it as stale;
> trust the factory file (OK) and the plain modified files (stale as
> expected).

---

## Sources

The family schemes are community knowledge, ported from open-source
projects. Attribution:

| Project | What we took |
|---|---|
| [nyetwurk/ME7Sum](https://github.com/nyetwurk/ME7Sum) | Bosch ME7 main checksum algorithm (descriptor blocks + (v, ~v) pair @ file_end−0x20) + its 88-bin validation corpus |
| [nyetwurk/IronFelix](https://github.com/nyetwurk/IronFelix) | The 7 family profiles in `ironfelix.py`: VAG ME7.XX subtypes, Bosch M3.x-5.x, Hyundai/China M7.9.x, Citroen ME7.4.5, Sagem |
| [NefMoto/NefMotoOpenSource](https://github.com/NefMoto/NefMotoOpenSource) | ME7 rolling/multirange checksum algorithms + the firmware code-pattern detection (C166 instruction parsing) |
| [Chookees/ECU_TCU_Files](https://github.com/Chookees/ECU_TCU_Files) | BMW E46 MS43/GS20 bins + real XDFs (checksum slots, region hints, golden diff) |
| [MS4X Wiki](https://www.ms4x.net) — Siemens GS20/SMGII Checksum Corrector | The GS20/SMG2 CRC-16/ARC algorithm (init, inclusive ranges, store offsets) — decompiled from the community tool |
| [MS4X Wiki](https://www.ms4x.net) + boot-code disassembly | The MS43 checksum scheme: descriptor tables, CRC-16/ARC, init words from the ID strings — cracked by disassembling the factory boot code with the C166 decoder |
| [bludgod/RomRaider](https://github.com/bludgod/RomRaider) | 501 factory Subaru ROMs — corpus for the Denso/Hitachi profiles |
| RomRaider source (`RomChecksum.java`) + [td-d/SubaruDefs](https://github.com/td-d/SubaruDefs) | The Denso Subaru descriptor-table checksum (SH72531 1 MB petrol + EE20 diesel) — 12-byte `[start][end][diff]` entries, BE32 word sums, target `0x5AA5A55A`, end-inclusive |

---

## Related commands

| Command | Reference |
|---|---|
| `openremap scan` | [→ scan.md](../scan/index.md) — file sorting by family |
| `openremap identify` | [→ identify.md](../identify/index.md) — manufacturer/SW identification |

---

