# Remaining Audit Items — Categorization

**Status: 🟡 In progress** (open items from the offset-matching audit.
As of 2026-08-20 the volatile classifier partially covers the
Checksum-Change and IMMO scanners — see the Summary below.)

**Date:** 2026-05-30
**Source:** `2026-05-30-offset-matching-audit.md` — items not yet implemented

---

## Tags

| Tag | Meaning |
|---|---|
| `needs-ecu-knowledge` | Requires per-ECU-family data (memory maps, checksum locations, instruction encoding). Blocked until we build the family knowledge base. |
| `deferred` | Genuinely useful but too large or risky to tackle now. Revisit when real-world data proves it's a pain point. |
| `not-worth-now` | The cost/benefit doesn't pencil out given current project stage. May never be worth it. |

---

## Items

### Architecture-Aware Masked Matching

**Tag:** `deferred`

Match context anchors with a per-byte mask — opcodes must match exactly, operands/padding are wildcarded. Requires per-architecture mini-disassembler (TriCore instruction decoding, C167 operand bitfields). High implementation surface. Risk of over-masking causing silent wrong-address writes. Revisit when cross-revision apply failures become a demonstrated problem.

### Fuzzy/Confidence-Based Matching

**Tag:** `not-worth-now`

Hamming distance, Levenshtein, or confidence-threshold matching in the patcher when exact match fails. Generic — needs no ECU knowledge. But any false positive in a binary patch writes to the wrong address and bricks the ECU. The safety of "exact match or fail" is a feature, not a bug. Force Save already provides an explicit user-controlled escape hatch.

### Structural/Landmark-Based Anchoring

**Tag:** `deferred`

Use stable structural features (axis patterns, table headers, function prologues) as search landmarks instead of raw byte offsets. The map hunter already detects axes generically — this could bootstrap from that. Complex to implement, but eliminates the ±2048 window constraint entirely. Revisit when the ±2048 window proves too restrictive for real-world cross-revision tunes.

### Section-Boundary-Aware Context Capture

**Tag:** `needs-ecu-knowledge`

Don't capture context that straddles code/data section boundaries. Requires per-family memory layout maps (where does the calibration section start/end? where is the code section?). These maps don't exist in the codebase yet.

### Post-Patch Checksum Recalculation

**Tag:** `needs-ecu-knowledge`

Automatically recalculate ECU checksums after patching. Each ECU family has a different checksum algorithm (EDC17: multi-block CRC32, ME7: single CRC16, etc.). The manufacturer extractors identify the family but don't yet know the checksum layout. Requires per-family checksum location + algorithm data.

### Checksum-Change Scanner (Annotator)

**Tag:** `needs-ecu-knowledge` — **partially covered (2026-08-20)**

Flag instructions that look like checksum corrections rather than calibration changes. Depends on knowing where checksum blocks live in each ECU family's binary layout.

**Partially covered:** the volatile-region classifier (`cook-volatile`,
`core/services/recipes/volatile.py`) now excludes/flag instructions
overlapping **verified checksum stores** (`CHECKSUM_STORE` kind) — VIN
records (`VIN`), ident-block strings (`SERIAL_OR_IDENT`) and low-entropy
counters (`COUNTER_OR_SERIAL`).  What remains: the per-family *repair*
side (recalculating checksums after patching) is still not built —
repair stays opt-in and out of scope for now.

### IMMO-Data Scanner (Annotator)

**Tag:** `needs-ecu-knowledge` — **partially covered (2026-08-20)**

Flag instructions overlapping immobilizer data blocks. IMMO location varies by ECU family — some in EEPROM emulation area, some in dedicated security blocks.

**Partially covered:** IMMO/serial stores are captured structurally via
the volatile classifier's `SERIAL_OR_IDENT` / `COUNTER_OR_SERIAL` kinds
(low-entropy, ASCII-shaped regions).  What remains: no *dedicated* IMMO
detector with per-family security-block knowledge — generic structural
flags only.

---

## Summary

| Item | Tag | Blocked By |
|---|---|---|
| Masked matching | `deferred` | Cross-revision data, disassembler investment |
| Fuzzy matching | `not-worth-now` | Inherent false-positive risk |
| Structural anchoring | `deferred` | Complexity, needs real-world pain point data — see the cross-firmware milestone (0.8.0) |
| Section-boundary context | `needs-ecu-knowledge` | Per-family memory maps |
| Checksum recalculation | `needs-ecu-knowledge` | Per-family checksum layouts + algorithms (detection ✅, repair opt-in, not built) |
| Checksum scanner | `needs-ecu-knowledge` | **✅ partially covered** by volatile classifier (`CHECKSUM_STORE`) |
| IMMO scanner | `needs-ecu-knowledge` | **✅ partially covered** by volatile classifier (`SERIAL_OR_IDENT` / `COUNTER_OR_SERIAL`) |

**Actionable now:** the volatile classifier (2026-08-20) covered the two
annotator items at the structural level.  Remaining items are either
blocked on per-family knowledge or deferred until real-world data
justifies the investment.  The low-hanging fruit (entropy-gated
expansion, uniqueness verification, Force Save, annotator scanners,
patcher anchor improvements, volatile classification) is done.
