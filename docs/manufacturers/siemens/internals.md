# Siemens Extractor Internals

This document covers detection strategies, binary format details, and internal
extractor structure. For the user-facing family reference, see [Supported Siemens ECU Families](index.md).

---

## Extractor directory structure

Each Siemens family lives in its own package under `src/openremap/tuning/manufacturers/siemens/<family>/`:

```/dev/null/directory-tree.txt#L1-8
siemens/
├── simtec56/   — Simtec 56 (Opel/Vauxhall X18XE, X20XEV petrol)
├── simos/      — SIMOS / SIMOS2 / SIMOS3 (VAG 1.4–1.6L petrol)
├── ppd/        — PPD1.x (VAG 2.0 TDI Pumpe-Düse diesel)
├── sid801/     — SID 801 / SID 801A (PSA/Ford 2.0/2.2 HDi diesel)
├── sid803/     — SID 803 / SID 803A (PSA/Ford/JLR 2.0/2.2 HDi diesel)
└── ems2000/    — EMS2000 (Volvo S40/V40/S60/S70/V70 T4/T5 turbo petrol)
```

The registry in `siemens/__init__.py` lists all extractors in priority order — most specific first. When a new binary is submitted, the first extractor whose `can_handle()` returns `True` wins.

---

## Registry ordering

The Siemens `EXTRACTORS` list is ordered by detection strength, strongest first:

| Priority | Extractor | Detection type | Size gate | Rationale |
|---|---|---|---|---|
| 1 | **Simtec56** | Strong positive | 128 KB exact | Strongest positive signatures (`5WK9` + `RS`/`RT` ident + header magic) at this size. Fully disjoint from all other families. |
| 2 | **SIMOS** | Positive keywords + header magic | 131 / 262 / 524 KB | Positive keyword detection (`SIMOS`, `5WP4`, `111s21`, `s21_`, `cas21`), falling back to header magic + size gate. Excludes `5WK9`, `5WS4`, `PPD`. |
| 3 | **PPD** | Strong positive | Variable (250 KB – 2 MB) | Strong positive signatures (`PPD1.`, `111SN`, `CASN`). Must come before EMS2000 and SID803 to claim 2 MB files with PPD signatures first. |
| 4 | **SID801** | Positive + header fallback | 512 KB exact | Positive signatures (`5WS4`, `PM3`) with header magic fallback for "dark" bins. Explicitly excludes `SID803`. |
| 5 | **SID803** | Positive signatures | 458 / 462 KB / 2 MB | Positive signatures (`111PO`, `PO2`, `PO3`, `S122`, `SID803`). Uses `PM3` as exclusion — `PM3` → always SID801, never SID803. |
| 6 | **EMS2000** | Exclusion-only | 256 KB exact | **MUST be last.** No positive ASCII signatures exist. Detection is by exclusion of every other known manufacturer + header magic match. Lowest confidence of all Siemens extractors. |

> **Rule:** EMS2000 must always be the last Siemens extractor. Moving it earlier would cause it to falsely claim binaries that a later, more specific extractor should handle.

---

## Simtec 56

### Overview

Opel/Vauxhall petrol ECUs (1995–2002) for X18XE and X20XEV engines. Vehicles include Vectra B, Astra F/G, Omega B, and Calibra. CPU is an Intel 8051 / Siemens C166 derivative.

### Binary format

| Property | Value |
|---|---|
| File size | Exactly 131 072 bytes (128 KB) |
| Header magic | `\x02\x00\xb0` (3 bytes — 8051 LJMP reset vector) |
| Full header | `\x02\x00\xb0\x20\xb2` (5 bytes — only first 3 checked; bytes 4–5 may vary) |
| Ident location | First 32 KB (ident area), but full-binary scan used due to compact size |

### Detection strategy (5-phase, all must pass)

1. **Size gate** — exactly 131 072 bytes.
2. **Exclusion** — reject if any Bosch / Delphi / Marelli signature found (`EDC17`, `MEDC17`, `ME7.`, `BOSCH`, `0261`, `MOTRONIC`, `PM3`, `PPD`).
3. **Detection signature** — require `5WK9` (Siemens part prefix) in the binary.
4. **Ident record** — require `RS` or `RT` prefix followed by an 8-digit GM part number. This distinguishes Simtec 56 from other `5WK9`-bearing families (SID801, etc.) that use different ident formats.
5. **Header magic** — first 3 bytes must be `\x02\x00\xb0`. Weak secondary confirmation — not sufficient on its own.

### Ident record format

The RS/RT ident record is the single most reliable identifier. Format:

```/dev/null/simtec56-ident-format.txt#L1-5
R[ST]<8-digit GM part> <12-16 digit serial>[a-z]5WK9<4-6 digits>

Example: RS90506365 0106577255425b5WK907302
         ││││││││││ │││││││││││││││││││││││
         ││││││││││ │││││││││││││││5WK90730 ← Siemens part (core 8 chars)
         ││││││││││ │0106577255425b ← serial (12–16 digits + lowercase letter)
         │90506365 ← GM part number (8 digits)
         RS ← prefix (RS = standard track, RT = alternate/test track)
```

The trailing digits after `5WK9` encode a 4-digit variant number plus a 1–2 digit checksum suffix. All confirmed samples show 5 trailing digits (4 variant + 1 check).

### Extracted fields

| Field | Source | Example |
|---|---|---|
| `hardware_number` | `5WK9` + 4 digits from ident record | `5WK90730` |
| `software_version` | Serial portion of ident record | `0106577255425b` |
| `oem_part_number` | 8-digit GM part from ident record | `90506365` |
| `serial_number` | Same as `software_version` | `0106577255425b` |
| `calibration_id` | Extended `S001xxxxxx` code, or short `Sxxxxx` ref | `S001005674` |
| `engine_code` | `X` + 2 digits + 2–3 uppercase letters | `X18XE`, `X20XEV` |
| VIN | 17-character ISO 3779 starting with `W` (Opel) | `W0L0JBF19W5117067` |

### Sub-field extraction

Sub-fields (GM part, serial, Siemens part) are split from the full ident record match using a compiled regex rather than separate lookbehind patterns — Python's `re` module does not support variable-length lookbehinds.

---

## SIMOS

### Overview

VAG (VW/Audi/Skoda/Seat) petrol ECUs from the late 1990s through mid-2000s, covering 1.4–1.6L engines. Three binary sub-types exist by file size.

### Binary sub-types

| Sub-type | Size | Header prefix | Notes |
|---|---|---|---|
| SIMOS EEPROM | 131 072 (128 KB) | `\x02` (1 byte) | 8051 reset vector style. Known variants: `\x02\x58\x95\x05`, `\x02\x56\x9f\x05`. Very sparse ASCII. |
| SIMOS 2.x | 262 144 (256 KB) | `\xc0\x64` or `\xfa\x00` (2 bytes) | EEPROM dumps. Known headers: `\xc0\x64\xa8\x20` (Golf 4), `\xfa\x00\x32\x04` (Octavia). Identifiers typically in filename only. |
| SIMOS 3.x | 524 288 (512 KB) | `\xf0\x30` (2 bytes) | Full flash. Common prefix to ALL 524 KB bins. Known variants: `\xf0\x30\xe8\x44`, `\xf0\x30\x58\x74`, `\xf0\x30\xa0\x4c`, `\xf0\x30\xc0\x6c`. |

### Detection strategy (layered, fast-to-slow)

1. **Exclusion** — reject if any known Bosch / SID / PPD / Simtec signature found. Scans first 512 KB for speed. Exclusion list includes: `EDC17`, `MEDC17`, `MED17`, `ME7.`, `BOSCH`, `0261`, `MOTRONIC`, `PM3`, `PPD`, `5WS4`, `5WK9`, `SID80`.
2. **Positive keywords** — if any definitive keyword is found anywhere in the binary, accept immediately:
   - `SIMOS` — ECU family string
   - `5WP4` — Siemens SIMOS part number prefix
   - `111s21` — project code with leading sequence number
   - `s21_` — project code with underscore separator
   - `cas21` — calibration dataset prefix
3. **Header magic + size gate** — for "dark" bins with no ASCII:
   - 524 KB + first 2 bytes `\xf0\x30` → accept
   - 262 KB + first 2 bytes `\xc0\x64` or `\xfa\x00` → accept
   - 131 KB + first byte `\x02` → accept

> **"Dark" bins:** Most SIMOS binaries contain no readable ASCII strings at all. Detection relies heavily on header magic + exclusion for these files. When strings ARE present, they are definitive identifiers.

### Patterns (when strings are present)

| Pattern | Format | Example |
|---|---|---|
| `siemens_part` | `5WP4` + 3–5 digits | `5WP4860`, `5WP40123` |
| `simos_label` | `SIMOS` + whitespace + 4-digit sub-version | `SIMOS   2441` |
| `oem_part_number` | VAG part: `0[46][7A]906` + 3 digits + optional suffix | `06A906019BH`, `047906019` |
| `project_code` | `s21` + digit/underscore + up to 6 alphanumeric | `s21_2441`, `s2114601` |
| `calibration_dataset` | `cas21` + 3 digits + `.DAT` | `cas21146.DAT` |
| `serial_code` | `6577` + 6 digits | `6577295501` |
| `oem_ident` | Full OEM string (part + displacement + config + SIMOS) | `06A906019BH 1.6l R4/2V SIMOS   2441` |

### Family resolution

The `ecu_family` field is resolved with cascading priority:

1. Specific SIMOS label from binary (e.g. `SIMOS   2441` → normalised to `SIMOS 2441`).
2. Generic `SIMOS` keyword + size inference (262 KB → `SIMOS2`, 524 KB → `SIMOS3`).
3. Size-only inference when no SIMOS string exists.
4. Fallback: `SIMOS`.

---

## PPD1.x

### Overview

Siemens/VDO diesel ECUs for VAG 2.0 TDI PD (Pumpe-Düse / unit injector) engines, approximately 2003–2008. Three sub-variants: PPD1.1, PPD1.2, PPD1.5. Succeeded by Continental SID families when common-rail replaced unit injection.

### Binary format

| Property | Value |
|---|---|
| File sizes | 249 856 (~250 KB), 2 097 152 (2 MB), 2 097 154 (2 MB + 2) |
| Ident location | First 64 KB in 250 KB bins; offset `~0x040000` in 2 MB bins |
| Search regions | Header (first 4 KB), ident area (first 320 KB), full binary |

### Detection strategy

1. **Exclusion** — reject if any Bosch / Marelli signature found in the first 512 KB (`EDC17`, `MEDC17`, `MED17`, `ME7.`, `BOSCH`, `PM3`).
2. **Positive detection** — accept if at least one PPD signature is present anywhere in the full binary:
   - `PPD1.` — definitive PPD family identifier
   - `111SN` — repeated SN project code blocks
   - `CASN` — calibration dataset prefix

No file-size gate is applied — detection is purely signature-based (after exclusion).

### Ident record format

```/dev/null/ppd-ident-format.txt#L1-7
<serial>--    111SN<project>111SN<project>111SN<project>CASN<cal>.DAT    <VAG part> R4 <disp> PPD1.<ver>

Example:
  6576286135--    111SN100K5400000111SN100K5400000111SN100K5400000CASN1K54.DAT    03G906018DT R4 2.0l PPD1.2
  ││││││││││      │││││││││││││││                                ││││││││││││    │││││││││││ ││ ││││ │││││││
  │                │SN project codes (repeated 3×)                │CASN1K54.DAT   │03G906018DT│  │2.0l│PPD1.2
  │6576286135 ← serial code (10 digits)                           │← calibration   │← OEM part│  │    │← family
                                                                                               │R4 ← config
```

### Extracted fields

| Field | Source | Example |
|---|---|---|
| `ecu_family` | `PPD1.\d` family string | `PPD1.2` |
| `software_version` | 10-digit serial code (`6576` + 6 digits) | `6576286135` |
| `oem_part_number` | VAG part (`03G906` + 3 digits + optional suffix) | `03G906018DT` |
| `calibration_id` | `CASN` dataset filename | `CASN1K54.DAT` |
| `hardware_number` | Dot-delimited version string (when present) | `0431657628.90.02` |
| `displacement` | Engine displacement string | `R4 2.0l` |

---

## SID 801 / SID 801A

### Overview

PSA (Peugeot/Citroën) and Ford HDi diesel ECUs (DW10/DW12 engines, 2.0 HDi and 2.2 HDi), approximately 2001–2006. SID801A is a minor hardware revision — binary format is identical.

### Binary format

| Property | Value |
|---|---|
| File size | Exactly 524 288 bytes (512 KB) |
| Header magic (Type A) | `\xc0\xf0\xa0\x14` — "dark" bins, no embedded ASCII ident |
| Header magic (Type B) | `\xfa\x00\x46\x04` — bins with embedded 5WS4 ident record |
| Ident location | First 4 KB (header) for ident record; first 128 KB for PM3 codes |

### Two binary sub-types

| Type | Header | 5WS4 ident? | Detection |
|---|---|---|---|
| **Type A** | `\xc0\xf0\xa0\x14` | No embedded ASCII ident | Detected by header magic fallback only |
| **Type B** | `\xfa\x00\x46\x04` | Yes — full ident record | Detected by `5WS4` / `PM3` positive signatures |

### Detection strategy (4-phase)

1. **Size gate** — exactly 524 288 bytes. Eliminates >99% of non-SID801 bins.
2. **Exclusion** — reject if any exclusion signature found in full binary: `EDC17`, `MEDC17`, `MED17`, `ME7.`, `SID803`.
3. **Positive signatures** — search first 128 KB for `5WS4` (hardware part prefix) or `PM3` (project code prefix). First hit → accept.
4. **Header magic fallback** — for "dark" Type A bins with no embedded signatures, accept if first 4 bytes match either known SID801 header.

### Ident record format (Type B only)

```/dev/null/sid801-ident-format.txt#L1-5
5WS4xxxxX-T <9-digit serial> <date+serial>S2<version>

Example: 5WS40145A-T 244177913   04020028014941S220040001C0
         │││││││││││ │││││││││   ││││││││││││││││││││││││││
         │5WS40145A-T│244177913  │04020028014941 ← date/serial block
         │← hardware  │← software version (9-digit serial — PRIMARY MATCH KEY)
                                               S220040001C0 ← S-record version
```

The 9-digit serial is unique per software calibration release and serves as the primary matching key for recipe lookup.

### Extracted fields

| Field | Source | Example |
|---|---|---|
| `hardware_number` | `5WS4` part from ident record | `5WS40145A-T` |
| `software_version` | 9-digit serial from ident record (primary match key) | `244177913` |
| `ecu_family` | Explicit family string or default | `SID801`, `SID801A` |
| `calibration_id` | PM3 project code or CAPM dataset reference | `PM38101C00`, `CAPM3630.DAT` |
| `oem_part_number` | PSA part number (`96` + 8 digits) | `9648608680` |
| `serial_number` | S-record reference (`S118`/`S120`/`S220` prefix) | `S118430100` |

### Project and S-record references

| Reference type | Format | Examples |
|---|---|---|
| Project code | `PM3` + 4–5 digits + optional suffix | `PM38101C00`, `PM33001C00`, `PM363000` |
| PM block marker | `111PM3` + 4–6 digits | `111PM3210050`, `111PM3280000` |
| Calibration dataset | `CAPM3` + 3–4 digits + `.DAT` | `CAPM3630.DAT`, `CAPM3930.DAT` |
| S-record (software) | `S118` / `S120` + 6–10 digits | `S118430100`, `S120040001` |
| S-record (data) | `S220` + 6–10 digits + optional suffix | `S220040001C0` |

---

## SID 803 / SID 803A

### Overview

PSA (Peugeot/Citroën), Ford, and Jaguar/Land Rover diesel ECUs from the mid-2000s onward. Two sub-groups distinguished by file size: SID803 (smaller, 458–462 KB) and SID803A (2 MB full flash).

### Binary sub-groups

| Sub-group | File sizes | Characteristics |
|---|---|---|
| **SID803** | 458 752 (448 KB), 462 848 (452 KB) | PO project codes, 111PO block markers, S120 S-records. No embedded 5WS4 in some files. 4 KB size difference between variants is padding. |
| **SID803A** | 2 097 152 (2 MB) | 5WS4 hardware idents in header (`5WS40262B-T`, `5WS40612B-T`), S122 S-records, FOIX references, CAPO calibration datasets. |

### Detection strategy (3-phase)

1. **Size gate** — file size must be one of 458 752, 462 848, or 2 097 152 bytes. Immediate rejection otherwise.
2. **Exclusion** — reject if any exclusion signature found in the first 512 KB:
   - `EDC17`, `MEDC17`, `MED17`, `ME7.` — Bosch families that may share file sizes
   - `PM3` — **strongest negative signal.** If `PM3` is present, the binary is SID801, never SID803. This is the single definitive discriminator between the two Siemens diesel families.
3. **Detection signatures** — accept if at least one is found in the first 512 KB:
   - `111PO` — PO block marker (highest specificity)
   - `PO2` — PO2xx project code prefix
   - `PO3` — PO3xx project code prefix
   - `S122` — S122-series S-record reference (SID803A specific)
   - `SID803` — explicit family string (not always present)

### SID801 vs SID803 — the PM3/PO discriminator

```/dev/null/sid-discriminator.txt#L1-4
PM3 present → always SID801, never SID803
PO  present → always SID803, never SID801

SID801 excludes SID803; SID803 excludes PM3. The two are fully disjoint.
```

### Ident record format (SID803A, 2 MB files only)

```/dev/null/sid803a-ident-format.txt#L1-3
5WS4xxxxX-T  <14-17 digit serial>

Example: 5WS40262B-T  00012345678901234
```

The serial portion (14–17 digits) is extracted as `software_version` and used as the primary match key.

### Extracted fields

| Field | Source | Example |
|---|---|---|
| `hardware_number` | `5WS4` part from header (2 MB files) | `5WS40262B-T` |
| `software_version` | 14–17 digit serial from ident record | `00012345678901234` |
| `ecu_family` | Resolved from file size + binary signatures | `SID803`, `SID803A` |
| `calibration_id` | CAPO dataset or PO project code | `CAPO1234`, `PO220` |
| `s_record_ref` | S-record reference | `S1200790100E0`, `S122001234AB` |
| `foix_ref` | Factory/OEM identification cross-reference | `FOIXS160001225B0` |

### S-record series comparison

| Family | S-record series | Example |
|---|---|---|
| SID801 | S118, S120, S220 | `S118430100`, `S120040001`, `S220040001C0` |
| SID803 | S120 | `S1200790100E0` |
| SID803A | S122 | `S122001234AB` |

The higher S122 series is a distinguishing marker for SID803A versus SID801's S118/S120 range.

---

## EMS2000

### Overview

Volvo S40/V40/S60/S70/V70 T4/T5 turbo petrol ECUs (1996–2004). Also known as Siemens EMS2000 / Fenix 5. This is a **"dark" ECU family** — the binary is essentially pure machine code and calibration data with almost no embedded ASCII metadata.

### Binary format

| Property | Value |
|---|---|
| File size | Exactly 262 144 bytes (256 KB) |
| Header magic | `\xc0\xf0\x68\xa6` (4 bytes — single known sample) |
| Content | Pure machine code + calibration data. No ident block, no metadata headers, no embedded part numbers. |
| Part numbers | S108xxxxx format (Siemens) — found in filename only, never embedded in binary |

### Detection strategy (exclusion-only, 3-phase)

1. **Size gate** — exactly 262 144 bytes.
2. **Exclusion** — reject if ANY known manufacturer signature is found anywhere in the full binary. The exclusion list is comprehensive:
   - **Bosch modern:** `EDC17`, `MEDC17`, `MED17`, `ME7.`, `BOSCH`, `0261`, `0281`, `MOTRONIC`, `SB_V`, `Customer.`
   - **Bosch legacy:** `/M1.`, `/M2.`, `/M3.`, `/M4.`, `/M5.` (DAMOS strings)
   - **Siemens SIMOS:** `5WP4`, `SIMOS`, `s21`, `cas21`
   - **Siemens SID:** `5WS4`, `PM3`, `PO`, `SID80`
   - **Siemens PPD:** `PPD`, `SN1`, `CASN`, `03G906`
   - **Siemens Simtec:** `5WK9`
   - **Siemens internal:** `111PM`, `111PO`, `111SN`, `111s2`, `CAPM`, `CAPO`
   - **Delphi/Delco:** `DEL`, `DELCO`, `DELPHI`
   - **Marelli:** `MAG`, `MARELLI`, `IAW`
   - **Denso:** `DENSO`
3. **Header magic** — accept only if first 4 bytes match `\xc0\xf0\x68\xa6`. This is based on a single confirmed sample — not guaranteed to be consistent across all EMS2000 variants.

> **Lowest confidence extractor.** EMS2000 produces no `software_version`, no `match_key`, and minimal metadata. The extractor exists primarily to prevent EMS2000 bins from being classified as "Unknown."

### Extracted fields

| Field | Source | Value |
|---|---|---|
| `ecu_family` | Fixed | `EMS2000` |
| `hardware_number` | — | `None` (not embedded) |
| `software_version` | — | `None` (not embedded) |
| `calibration_id` | — | `None` (not embedded) |
| `serial_number` | Volvo VIN if found (`YV1` + 14 chars) | Usually `None` |
| `match_key` | — | Always `None` (no `software_version`) |

The only pattern searched is `volvo_vin` (`YV1` + 14 alphanumeric characters), and most EMS2000 dumps do not contain the VIN.

---

## Confidence scoring internals

### Dark bin impact

Siemens families are disproportionately affected by the "dark bin" phenomenon — binaries with little or no readable ASCII content. This affects confidence scoring:

| Family | Dark bin frequency | Typical confidence | Notes |
|---|---|---|---|
| Simtec 56 | Rare | High | RS/RT ident record is almost always present |
| SIMOS | Very common | Medium–Low | Most bins detected by header magic alone |
| PPD1.x | Rare | High | Rich ident record with serial, OEM part, family string |
| SID 801 Type A | Always | Medium | No embedded ident; header-only detection |
| SID 801 Type B | Never | High | Full 5WS4 ident record present |
| SID 803 | Sometimes | Medium–High | PO codes present; 5WS4 may be absent in smaller files |
| SID 803A | Rare | High | Full 5WS4 ident in 2 MB files |
| EMS2000 | Always | Low | No metadata at all; exclusion-only detection |

### IDENT BLOCK MISSING warning

Fires when `software_version` is `None` for families where absence is abnormal. For Siemens, this applies to:

`Simtec56` · `SIMOS` · `SIMOS2` · `SIMOS3` · `PPD1.1` · `PPD1.2` · `PPD1.5` · `SID801` · `SID801A` · `SID803` · `SID803A`

**Exception:** `EMS2000` — software version absence is normal and expected. No warning is emitted.

---

## Cross-family disambiguation

Several Siemens families share file sizes or structural features. The table below summarises how each potential conflict is resolved:

| Conflict | Shared property | Resolution |
|---|---|---|
| Simtec 56 vs SIMOS EEPROM | Both 131 072 bytes | Simtec 56 requires `5WK9` + `RS`/`RT` ident; SIMOS excludes `5WK9`. Fully disjoint. |
| SIMOS 2.x vs EMS2000 | Both 262 144 bytes | SIMOS has positive keywords or `\xc0\x64`/`\xfa\x00` header; EMS2000 excludes all SIMOS signatures. |
| SIMOS 3.x vs SID801 | Both 524 288 bytes | SIMOS requires `\xf0\x30` header and excludes `5WS4`/`PM3`; SID801 requires `5WS4`/`PM3` or own header magic. |
| PPD1.x vs SID803A | Both can be 2 097 152 bytes | PPD has `PPD1.` / `111SN` / `CASN`; SID803 has `111PO` / `PO2` / `PO3` / `S122`. PPD is checked first in registry order. |
| SID801 vs SID803 | Both Siemens diesel, some shared patterns | `PM3` → SID801; `PO` → SID803. SID801 excludes `SID803`; SID803 excludes `PM3`. Fully disjoint. |

---

← [Back to family reference](index.md) · [Back to README](../../README.md)