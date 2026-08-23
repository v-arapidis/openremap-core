# Confidence Scoring

Every `openremap identify` result and every `openremap scan` line includes a
confidence assessment — a measure of how reliably the system detected and
identified the ECU binary, based on detection quality, extracted identity
fields, filename heuristics, and map-structure analysis.

> **What confidence measures:** The score reflects identification quality —
> how many expected identity fields were successfully extracted and how
> rigorous the detection cascade was.  It does **not** measure whether the
> binary content has been modified.  A tuned file with all ident blocks
> intact will score just as highly as an unmodified one.

---

## Tiers

| Tier | What it means |
|---|---|
| **HIGH** | All key identifiers present and consistent — strong identification |
| **MEDIUM** | Most identifiers present, minor gaps |
| **LOW** | Some identifiers missing, or a mild filename concern |
| **SUSPICIOUS** | Significant identification gaps or filename red flags — inspect before use |
| **UNKNOWN** | No extractor matched the binary — family not supported |

---

## Example output

### HIGH — Bosch EDC17, all identifiers present

```
  ── Confidence ─────────────────────────────────────
  Tier   HIGH
  Score  75
  Signal  +15  STRONG detection (6-phase cascade)
  Signal  +30  canonical SW version (1037-prefixed, Bosch)
  Signal  +20  hardware number present (0261209352)
  Signal  +10  ECU variant identified (EDC17C66)
```

### HIGH — Delphi Multec S, all identifiers present

```
  ── Confidence ─────────────────────────────────────
  Tier   HIGH
  Score  65
  Signal  +15  STRONG detection (4-phase cascade)
  Signal  +30  canonical SW version (12345678, Delphi 8-digit GM-style)
  Signal  +20  hardware number present (09391237)
```

### SUSPICIOUS — stop and check

```
  ── Confidence ─────────────────────────────────────
  Tier   SUSPICIOUS
  Score  -20
  Signal   +5  WEAK detection (minimal checks)
  Signal  -15  SW ident absent — no match key produced
  Signal  -25  tuning/modification keywords in filename
  ⚠  IDENT BLOCK MISSING
  ⚠  TUNING KEYWORDS IN FILENAME
```

---

## Signals

Each signal line shows what contributed to the tier. A `+` prefix raised
confidence; a `-` prefix lowered it.

### Detection quality baseline

The rigour of the extractor's `can_handle()` method sets a baseline score
before any identity fields are examined.

| Detection quality | Delta | Criteria |
|---|---|---|
| STRONG | +15 | 4+ phase detection cascade with unique byte signatures (e.g. Multec S, IAW 1AV) |
| MODERATE | +10 | 2–3 phase detection (e.g. ME7.x, EDC15) |
| WEAK | +5 | Minimal checks or broad heuristic match (e.g. M3.x, PPD) |

### Software version

| Signal | Delta | Notes |
|---|---|---|
| Canonical SW version present | +30 | Manufacturer-aware format check (see below) |
| Non-canonical SW version present | +15 | SW found but does not match expected format |
| SW absent, expected by family profile, no match_key | -15 | Family normally stores SW and no fallback matched |
| SW absent, expected by family profile, match_key from fallback | -10 | Family normally stores SW but a fallback key was produced |
| SW absent, NOT expected by family profile | 0 | Family architecturally omits SW — no penalty |

### Other identity fields

| Signal | Delta |
|---|---|
| Hardware number present | +20 |
| OEM part number present | +5 |
| ECU variant identified | +10 |
| Calibration ID present | +10 |

### Filename signals

These are the only signals that relate to file originality rather than
identification quality.  They flag filenames that suggest the binary may
have been modified or provide no identifying context.

| Signal | Delta |
|---|---|
| Tuning keywords in filename (`stage`, `remap`, `tuned`, `disable`, …) | -25 |
| Generic numbered filename (`1.bin`, `42.bin`, …) | -15 |

---

## Warnings

Warnings flag specific concerns, independent of the tier score:

| Warning | What it means |
|---|---|
| `⚠ IDENT BLOCK MISSING` | SW version absent for a family that always stores one — the ident block may be wiped or unreadable |
| `⚠ TUNING KEYWORDS IN FILENAME` | Filename contains words associated with modified files (`stage`, `remap`, `tuned`, `evc`, `disable`, …) |
| `⚠ GENERIC FILENAME` | Bare numbered filename (`1.bin`, `42.bin`) provides no identifying context |

---

## How the score is calculated

Each signal carries a positive or negative delta. The deltas are summed into a
raw numeric score, which is then mapped to a tier:

| Score range | Tier |
|---|---|
| ≥ 55 | HIGH |
| 25 – 54 | MEDIUM |
| 0 – 24 | LOW |
| < 0 | SUSPICIOUS |
| no extractor matched | UNKNOWN |

The raw score is available in JSON output (`openremap identify --json`) under
`confidence.score`, alongside the `tier` string and the full `signals` array.

---

## Family field profiles

Each ECU family declares a **field profile** — the set of identity fields that
the ECU architecturally stores in its binary. Fields not in the profile are
never penalized when absent.

For example, the IAW 1AP family only stores a calibration fingerprint. It does
not contain a discrete software version or hardware number in the binary. Under
the old scoring system this absence would have incurred penalties, dragging the
tier down unfairly. With field profiles, the absence of SW and HW for IAW 1AP
scores **0** instead of a penalty, allowing these families to reach their
natural tier based on the fields they *do* provide.

Families like Bosch EDC17 or ME7 declare SW, HW, variant, and calibration
in their profile — so all four are expected and scored accordingly. A family
like EMS2000 that only stores a variant and calibration will not be penalized
for missing SW or HW.

---

## Detection quality

The extractor's `can_handle()` method varies in rigour across families. A
6-phase detection cascade that checks magic bytes, block boundaries, address
maps, and internal checksums provides much stronger evidence of a correct match
than a 2-phase heuristic that only checks file size and a single byte pattern.

The detection quality baseline reflects this difference:

- **STRONG** (+15): Extractors like Multec S and IAW 1AV run 4+ independent
  checks with unique byte signatures. A match is very likely correct.
- **MODERATE** (+10): Extractors like ME7.x and EDC15 use 2–3 checks — solid
  but not exhaustive.
- **WEAK** (+5): Extractors like M3.x and PPD rely on minimal or broad
  heuristics. A match is plausible but less certain.

---

## Manufacturer-aware scoring

The confidence system is **manufacturer-aware** — each manufacturer has its own
canonical software version pattern, and each ECU family declares its own field
profile. This ensures that all manufacturers can reach HIGH tier when their
binaries contain the expected identifiers.

### Canonical SW version formats by manufacturer

| Manufacturer | Canonical format | Examples |
|---|---|---|
| **Bosch** | Prefixed with `1037`, `1039`, `1267`, `1277`, `2227`, or `2537` | `1037374218`, `1039S65581` |
| **Delphi** | 8-digit GM-style SW number | `12345678`, `09391237` |
| **Siemens** | 9-digit serial or `5WK9`-prefixed | `5WK91234A`, `123456789` |
| **Magneti Marelli** | Family-specific short formats | varies by ECU family |

Any SW version that matches its manufacturer's canonical format earns +30.
A SW version that is present but does not match the expected format earns +15.
This replaces the previous approach where only Bosch `1037`-prefixed versions
received the full canonical bonus and all other manufacturers were capped at
the non-canonical score.

---

## Using confidence in practice

```bash
# Full per-signal breakdown for a single file
openremap identify ecu.bin

# Confidence as JSON — score, tier, signals array, warnings
openremap identify ecu.bin --json

# Triage an entire folder — confidence tag on every line
openremap scan ./my_bins/

# Export confidence scores and warnings for every file to CSV
openremap scan ./my_bins/ --report report.csv
```

A `SUSPICIOUS` result is not a verdict — it is a prompt to look closer.
Run `openremap identify` on any flagged file for the full signal breakdown
before deciding whether to use it.

---

← [Back to CLI reference](../getting-started/cli.md)