# Bosch Extractor Internals

This document covers detection strategies, OEM-specific format notes, and internal
extractor structure. For the user-facing family reference, see [Supported Bosch ECU Families](index.md).

---

## Extractor directory structure

Each Bosch family lives in its own package under `src/openremap/tuning/manufacturers/bosch/<family>/`:

```/dev/null/directory-tree.txt#L1-17
bosch/
├── edc1/          — EDC1 / EDC2
├── edc15/         — EDC15 (Format A + B + C / Volvo EDC15C3)
├── edc16/         — EDC16 (VAG C8/C39/PD, BMW C31/C35, Opel C9)
├── edc17/         — EDC17 / MEDC17 / MED17 / ME17 / MD1
├── edc3x/         — EDC 3.x (VAG HEX, BMW numeric, Opel cal block)
├── lh/            — LH-Jetronic
├── m1x/           — M1.x (BMW E28/E30/E34/E36, Opel petrol)
├── m1x55/         — M1.55 (Alfa Romeo)
├── me155/         — ME1.5.5 (Alfa Romeo/Fiat petrol)
├── m2x/           — M2.x (VW/Audi M2.9, Porsche M2.3, Opel M2.7/M2.8/M2.81)
├── m3x/           — M3.x (BMW M3.1/M3.3 and PSA/Citroën MP3.2/MP7.2)
├── m4x/           — M4.x (Volvo 850/960/S70/V70/S60/S80 petrol)
├── m5x/           — M5.x / M3.8x
├── mp9/           — MP9 (Motronic MP 9.0)
├── me7/           — ME7 / ME7.x (including ME7.6.2 for Opel Corsa D)
├── me9/           — ME9 (full flash, RamLoader)
├── mono/          — Mono-Motronic (single-point injection)
└── motronic_legacy/  — DME-3.2, M1.x-early, KE-Jetronic, EZK
```

The registry in `bosch/__init__.py` lists all extractors in priority order — most specific first. When a new binary is submitted, the first extractor whose `can_handle()` returns `True` wins.

---

## Confidence scoring internals

### Software version bonus

The `+30` canonical SW bonus is awarded when `software_version` matches a manufacturer-aware canonical SW regex:

```/dev/null/confidence-rules.txt#L1-2
+30  software_version matches canonical SW pattern             (EDC15, EDC16, EDC17, ME7, ME9, …)
+15  software_version present but non-canonical                (M2.x uses 1267/2227; M3.x uses 1267/2227; EDC3x uses cal numbers)
```

The `1039` prefix is used by PSA/Peugeot-Citroën EDC16C34 variants (e.g. Peugeot 3008 1.6 HDI, SW `1039398238`). The `1277` prefix is used by Italian-market ME7.3 variants (e.g. Ferrari 360, SW `1277356302`). Both are treated as equally canonical as `1037`.

M2.x, M3.x, and EDC 3.x families always produce the `+15` signal — their SW versions never begin with `1037`/`1039`/`1277` and that is expected, not a defect.

> **Note:** The confidence system now uses manufacturer-aware canonical SW recognition and detection strength baselines — see [confidence.md](../../concepts/confidence.md) for full details.

### Hardware number bonus

The `+20` hardware number bonus is awarded when `hardware_number` is present in the identification result.

### Tier thresholds

| Tier | Score |
|---|---|
| **High** | ≥ 55 |
| **Medium** | 25–54 |
| **Low** | 0–24 |
| **Suspicious** | < 0 |

> See [confidence.md](../../concepts/confidence.md) for the full scoring specification.

### IDENT BLOCK MISSING warning

A separate concept from the bonus above. The warning fires when `software_version` is `None` for any family listed below, because absence is abnormal for those platforms regardless of their SW prefix format. **Exception:** EDC15 Format C (Volvo EDC15C3) bins legitimately have no `software_version`; their `calibration_id` is used as the match-key fallback, so they still produce a valid `match_key` despite the warning.

`EDC15` · `EDC16` · `EDC17` · `MEDC17` · `MED17` · `ME17` · `ME9` · `MED9` · `ME7` · `ME3` · `ME5` · `M1X` · `M2X` · `M3X` · `M5X` · `MP9` · `EDC3`

Families where SW absence is normal (no `IDENT BLOCK MISSING` warning):

`LH-Jetronic` · `Motronic Legacy`

---

## Opel/GM format notes

Opel ECUs from this era span multiple Bosch families and each uses a distinct ident layout:

| ECU | Family | SW format | HW in binary? |
|---|---|---|---|
| Astra 2.0 DTI / Vectra 1.9 TDI | EDC3 | 7-digit cal number (e.g. `0770164`) | No — filename only |
| Vectra-C / Signum / Astra-H CDTI | EDC16C9 | Alphanumeric `1037` (e.g. `1037A50286`) | Yes — plain ASCII in cal area |
| Calibra 2.0T / Astra C20XE / Calibra 2.5 V6 / Omega 3.0 V6 | M2.7 / M2.8 / M2.81 | `1267xxxxxx` or `2227xxxxxx` | Yes — embedded in ident block |
| Corsa D 1.6T (ME7.6.2) | ME7.6.2 | `1037xxxxxx` | Yes — ZZ ident block (may be past 512 KB mark) |
| Corsa C 1.0 12V / Astra G petrol | M1.5.5 | 8-digit GM number (e.g. `90532609`) | Yes — GM ident block at `~0xD801` |

For split-ROM EDC3 chips (HHH / LLL or h / l suffix pairs), both physical chips store the **same** 7-digit calibration ID. The byte immediately following the cal number (`H` = 0x48, `L` = 0x4C) is Bosch's built-in chip discriminator.

---

## PSA/Citroën format notes

PSA (Peugeot, Citroën) petrol ECUs from the early 1990s use the **M3.x** family, specifically the MP3.2 and MP7.2 sub-variants. These are completely distinct from modern PSA EDC17 diesel bins.

| Vehicle | ECU sub-family | SW format | Calibration ID |
|---|---|---|---|
| Citroën ZX 2.0 16V (0261200218) | MP3.2 | `1267xxxxxx` or `2227xxxxxx` | DAMOS block |
| Citroën Saxo 1.6i VTS | MP7.2 | `1037xxxxxx` | DAMOS block |
| Other PSA petrol (`0000000M3` marker, no explicit sub-tag) | MP3.x-PSA | `1267xxxxxx` or `2227xxxxxx` | DAMOS block when present |
| Peugeot 106 1.4 / early PSA petrol (HW `0261200203`) | MP3.1 / MP3.x-PSA | `1267xxxxxx` or `2227xxxxxx` | DAMOS block — Layout B |

Key identification details for MP3.2 (32 KB bins):

- Family marker `0000000M3` is embedded at approximately offset `0x1FF2`.
- The ident digit string immediately precedes the marker: `digits[0:10][::-1]` → HW, `digits[10:20][::-1]` → SW.
- The `M3.X` label uses `X` as a literal sub-variant character, not a placeholder.
- A DAMOS calibration block in the format `revision/unknown/MP3.2/dataset/...` is stored elsewhere and is used as `calibration_id`.

> **Important:** PSA MP3.2 bins share the same reversed-digit ident encoding as BMW M1.x and M3.x. The `0000000M3` family marker is the definitive discriminator — it is listed as an exclusion in the M1.x extractor.

---

## PSA ME7 sector dump formats

PSA ME7 ECUs appear in two non-standard dump formats in addition to full images.

### 64 KB PSA calibration sector

Standalone exports of the calibration sector (normally at offset `0x10000` in a full dump). File is exactly 64 KB with ZZ marker at offset `0x0` instead of `0x10000`.

| Detail | Value |
|---|---|
| Size | 64 KB (0x10000 bytes) |
| ZZ marker | Offset `0x0` |
| HW + SW | `\xC8`-prefixed ASCII block anywhere in the file |
| Example | Peugeot 206 1.6i 16v — HW `0261206942`, SW `1037353507` |

### 256 KB PSA ME7.4.x calibration sector

ME7.4.x PSA-variant ECUs (e.g. Peugeot 207 THP) use a compact PowerPC-style header with no ZZ block, no MOTRONIC label, and no embedded HW number.

| Detail | Value |
|---|---|
| Size | 256 KB (0x40000 bytes) |
| Record marker | `\x02\x00` at offset `0x18` |
| SW version | Plain ASCII `1037xxxxxx` at offset `0x1A` |
| HW number | Not present — filename only |
| Example | Peugeot 207 THP 1.6 150HP — SW `1037394738` |

> These 256 KB PSA ME7.4.x files must not be confused with M5.x / M3.8x (also 256 KB, but identified by a `MOTR`-style ident) or with EDC16 sector dumps (256 KB, identified by `\xDE\xCA\xFE` at `0x3D`).

---

## Volvo M4.x format notes

Volvo petrol ECUs from the mid-1990s to early 2000s use Bosch Motronic M4.x. Two sub-variants:

| Sub-family | Size | Vehicles | Era |
|---|---|---|---|
| M4.3 | 64 KB | Volvo 850, 960, early S70/V70 | 1994–1998 |
| M4.4 | 128 KB | Volvo S60, S70, V70, S80, XC70 | 1998–2002 |

### Key structural differences from other Motronic families

M4.x uses **sequential (direct) digit order** — the opposite of M1.x and M3.x reversed encoding:

| Family | Digit order | Example ident | Decoded HW | Decoded SW |
|---|---|---|---|---|
| **M4.x** | Sequential | `026120422510373552771270544` | `0261204225` (direct) | `1037355277` (direct) |
| M3.x | Reversed | `5220412620773553701` | `0261204225` (reversed) | `1037355277` (reversed) |

### DAMOS descriptor

M4.x bins contain a DAMOS slash-delimited descriptor string:

```/dev/null/damos-descriptor.txt#L1-2
44/1/M4.3/09/5033/DAMOS0C03//040398/
47/1/M4.4/05/5044/DAMOS0C04//150699/
```

Fields: `revision/sub/family/version/dataset/damos_label/.../date/`

The family field (`/M4.3/` or `/M4.4/`) is the primary detection anchor.

### Ident digit run

The contiguous digit run in the last ~2 KB:

| Digits | Field | Example |
|---|---|---|
| 0–9 | `hardware_number` | `0261204225` |
| 10–19 | `software_version` | `1037355277` |
| 20+ | `calibration_id` | `1270544` |

### Detection strategy

1. **Exclusion** — reject on any modern Bosch / ME7 / M5.x / M3.x / M2.x / MP9 / MOTRONIC signature
2. **Size gate** — exactly 64 KB or 128 KB
3. **DAMOS token** — accept on `/M4.3/` or `/M4.4/` anywhere in the binary
4. **Ident fallback** — accept on a valid sequential digit run (≥ 20 digits, HW starts `0261`, SW starts `1037`/`1267`/`2227`) in the last 2 KB

### Match key fallback

When `software_version` is absent, `calibration_id` is used as the match key via `match_key_fallback_field = "calibration_id"`.

---

← [Back to family reference](index.md) · [Back to README](../../README.md)