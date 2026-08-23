# Offset Matching Audit — Tune Applicability Decision Pipeline

**Status: ✅ Completed** (audit delivered 2026-05-30; findings recorded
and the priority mitigations — entropy-gated context, uniqueness
verification, Force Save escape hatch, annotator scanners — have since
been implemented.  Open follow-ups: [remaining audit items](2026-05-30-remaining-audit-items.md).)

**Date:** 2026-05-30
**Scope:** `openremap-core` — recipe cooking, validation, and patching pipeline
**Trigger:** Review of 32-byte context anchor matching risks (collision, pointer shift, code-boundary overlap)

---

## 1. Architecture Overview

The tune-applicability decision is a **3-phase pipeline** executed by the `tune` CLI command (`openremap/cli/commands/tune.py`). All phases operate entirely on in-memory bytes.

### Key Constants

| Parameter | Value | Defined |
|---|---|---|
| Context anchor size (`ctx`) | **32 bytes** (default) | `recipe_builder.py:217` — `ECUDiffAnalyzer(context_size=32)` |
| Search window radius | **±2048 bytes** | `patcher.py:38` — `EXACT_WINDOW = 2_048` |
| Matching algorithm | Exact byte-for-byte (`bytes.find()`) | `patcher.py:194` |
| Diff merge threshold | 16 bytes | `recipe_builder.py:326` — `merge_threshold=16` |

### Phase Flow

```
Target ROM + Recipe
       │
       ▼
┌──────────────────────────────────┐
│ Phase 1: ECUStrictValidator      │  validate_strict.py
│   check_file_size()              │  — size must match recipe's ecu.file_size
│   check_match_key()              │  — FAMILY::VERSION must match
│   validate_all()                 │  — EVERY instruction: read ob bytes at
│                                     exact offset, compare hex-for-hex
│   → safe_to_patch = True/False   │
└──────────────┬───────────────────┘
               │
        safe_to_patch?
          │          │
         YES         NO  →  ABORT (no bytes written)
          │
          ▼
┌──────────────────────────────────┐
│ Phase 2: ECUPatcher              │  patcher.py
│   detect_overlapping_writes()    │  — raise if any two instructions share
│                                     byte ranges
│   for each instruction:          │
│     _apply_instruction()         │
│       _find(ctx+ob, ±2048)       │  — anchor search, pick closest match
│       write mb at found offset   │
│   → all applied or ABORT         │  — partial buffer discarded on any failure
└──────────────┬───────────────────┘
               │
       all applied?
          │          │
         YES         NO  →  ABORT
          │
          ▼
┌──────────────────────────────────┐
│ Phase 3: ECUPatchedValidator     │  validate_patched.py
│   verify_all()                   │  — confirm every mb byte is present
│   → patch_confirmed = True/False │
└──────────────────────────────────┘
```

### Diagnostic Tool

`ECUExistenceValidator` (`validate_exists.py`) — searches the **entire** target binary for every `ob` pattern. Classifies each instruction:

| Status | Meaning |
|---|---|
| `EXACT` | `ob` found at the exact expected offset |
| `SHIFTED` | `ob` found in file but at a different offset (map moved) |
| `MISSING` | `ob` not found anywhere (wrong ECU or already modified) |

Verdict: `safe_exact` | `shifted_recoverable` | `missing_unrecoverable`

Designed to run AFTER strict validation fails, to diagnose WHY.

---

## 2. How Context Anchors Are Captured (Recipe Cooking)

File: `openremap-core/openremap/core/services/recipe_builder.py`

```python
# Line 217 — default context size
def __init__(self, ..., context_size: int = 32, ...):

# Lines 318-324 — blind positional capture
def _get_context(self, offset: int, size: int) -> Tuple[bytes, bytes]:
    ctx_start = max(0, offset - self.context_size)
    ctx_end = min(len(self.original_data), offset + size + self.context_size)
    before = self.original_data[ctx_start:offset]   # <-- ctx anchor
    after = self.original_data[offset + size : ctx_end]
    return before, after
```

The `ctx` field in each instruction is the **32 bytes immediately preceding the changed region** in the original binary. It is captured without any analysis of what those bytes contain — data, code, padding, pointers, or otherwise.

### Pre-Cook Guards

| Guard | Type | Behavior |
|---|---|---|
| Size match (`check_size_match`) | **Hard error** — raises `ValueError` | Original and modified must be same size |
| Identity match (`check_identity_match`) | **Warning only** | Compares `match_key` of both binaries; records warning if they differ |

---

## 3. How Anchors Are Matched (Patching)

File: `openremap-core/openremap/core/services/patcher.py`

```python
# Lines 174-204 — the core anchor search
def _find(self, ctx: bytes, ob: bytes, expected: int) -> tuple[int, int]:
    anchor = ctx + ob                              # atomic search pattern
    ctx_len = len(ctx)

    win_start = max(0, expected - EXACT_WINDOW)     # ±2048
    win_end = min(len(self._snapshot), expected + EXACT_WINDOW + len(anchor))
    region = self._snapshot[win_start:win_end]

    matches: List[int] = []
    pos = 0
    while True:
        p = region.find(anchor, pos)                # exact byte match only
        if p == -1:
            break
        matches.append(win_start + p + ctx_len)     # offset of ob start
        pos = p + 1

    if not matches:
        return -1, 0

    # Tiebreaker: closest to expected offset
    return min(matches, key=lambda o: abs(o - expected)), len(matches)
```

Key properties:
- Searches against a **frozen snapshot** of the original binary — earlier writes never corrupt later searches
- **`match_count > 1`** signals ambiguity (multiple hits in window)
- When `ctx` is empty/absent, falls back to **exact read at expected offset** (lines 222-224)
- The patcher searches for `ctx + ob` but writes `mb` at the position where `ob` starts (offset = match_pos + len(ctx))

---

## 4. Risk Assessment

### 4.1 Collision Problem (Low-Entropy Anchors) — MITIGATED AT COOK TIME

**Severity: HIGH** — can cause silent writes to wrong addresses, bricking the ECU.

The context anchor is captured blindly. If a calibration map sits near a padding region (filled with `0x00` or `0xFF`), the 32-byte `ctx` may be entirely padding bytes. The pattern `0x00*32 + ob` will match *every* padding region in the binary.

**Current "safeguard":** The patcher sets `ambiguous = True` when `match_count > 1`, but **still applies the write anyway** — it just attaches a warning string and picks the match closest to the expected offset. In a padded region, "closest" is essentially arbitrary.

```python
# patcher.py:244-253 — writes even when ambiguous
ambiguous = match_count > 1
self._buffer[offset : offset + size] = mb   # <-- applied regardless
if ambiguous:
    msg += f"WARNING: {match_count} ctx+ob matches found..."
```

**✅ IMPLEMENTED (2026-05-30): Entropy-gated context expansion.** The cook pipeline now has:

1. **Shannon entropy scoring** (`entropy.py:35-69`) — measures entropy in bits/byte. Returns 0.0 for uniform padding, 8.0 for random data. Threshold: 2.5 bits/byte.

2. **Geometric context expansion** (`entropy.py:131-223`, `find_unique_context`) — starts at `min_size=32`, doubles until both `entropy ≥ threshold` AND `ctx+ob` is unique in the entire binary, or until `max_size=512` is reached.

3. **Guard 3: non-unique anchor hard-stop** (`recipe_builder.py:533-557`) — when `require_unique=True` (default), raises `ValueError` listing every instruction whose anchor is still non-unique after 512-byte expansion. When `require_unique=False` (Force Save), records them as `cook_warnings` instead.

4. **Per-instruction metadata** stored in the recipe: `ctx_entropy`, `ctx_unique`, `ctx_expanded`, `context_size` (the *actual* size used, which may differ from the configured default).

This eliminates the blind-capture problem: anchors are now verified unique in the *original* binary before the recipe is written. However, this does NOT guarantee uniqueness in a *different* target binary (different SW revision) — that requires Priority 4 (masked matching).

### 4.2 Pointer/Offset Shift (Anchor Fragility) — PARTIALLY MITIGATED

**Severity: MEDIUM** — causes false negatives (patch rejected), not corruption.

If the 32-byte `ctx` window happens to contain a relative jump address, absolute pointer, or other compiled reference, a SW revision that changes that address will break the anchor. The `bytes.find()` returns nothing, and the instruction fails.

**Current mitigation:** The ±2048 search window handles the case where the *entire map* shifted, but NOT the case where the *anchor bytes themselves* changed. The window is a spatial tolerance, not a content tolerance.

**No fuzzy matching exists.** No Hamming distance, no Levenshtein, no confidence thresholds, no wildcarding. The match is binary: exact or nothing.

### 4.3 Code Boundary Overlap — UNMITIGATED

**Severity: MEDIUM** — causes false negatives when SW revision changes code near a map.

If a map is located near the boundary between a data section (calibration) and a code section (executable), the 32-byte `ctx` may straddle both. A compiler optimization in a new SW revision (loop unrolling, register reallocation) will change the code bytes, breaking the anchor.

The context capture has **no awareness of ECU memory layout** — no segment maps, no section boundaries, no distinction between code and data regions. The manufacturer extractors identify the ECU family but do not expose memory maps.

### 4.4 Additional Finding: The Annotator Is a Skeleton

**Severity: LOW (missed opportunity)**

File: `openremap-core/openremap/core/services/annotator.py`

The `RecipeAnnotator` defines a clean plugin architecture (`InstructionScanner` protocol, lines 55-75) but ships with exactly **one** scanner: `VINScanner`. Missing scanners that the architecture already supports:

| Missing Scanner | What It Would Catch |
|---|---|
| Low-entropy context scanner | Anchors in padding regions (risk 4.1) |
| Checksum detector | Changed bytes that are checksum corrections, not calibration changes |
| IMMO detector | Immobilizer data changes |
| Code-boundary scanner | Context windows that cross segment boundaries (risk 4.3) |
| Serial number scanner | ECU serial number changes |

### 4.5 Additional Finding: No Post-Patch Checksum Recalculation

**Severity: MEDIUM** — documented but unautomated.

The recipe builder explicitly warns (lines 46-57 of `recipe_builder.py`) that checksum bytes captured in the diff are checksums for the *modified* binary, not the target. Applying them to a different binary produces an invalid checksum. The user must use an external tool (WinOLS, ECM Titanium) to recalculate. This is a known gap, not a bug, but it's a bricking vector for users who don't read the warnings.

---

## 5. Capability Matrix

| Capability | Status | Location |
|---|---|---|
| Exact byte matching (`bytes.find()`) | ✅ Exists | `patcher.py:194` |
| ±2048 byte search radius | ✅ Exists | `patcher.py:38` |
| Ambiguity detection (`match_count > 1`) | ✅ Warns but still applies | `patcher.py:244-253` |
| File size guard | ✅ Hard error | `recipe_builder.py:233-255` |
| ECU identity guard (`match_key`) | ✅ Warning | `validate_strict.py:102-130` |
| VIN overlap detection | ✅ Exists | `annotator.py:91-151` |
| Immutable snapshot for search consistency | ✅ Exists | `patcher.py:105` |
| Overlapping write detection | ✅ Exists | `patcher.py:275-308` |
| Post-patch verification | ✅ Exists | `validate_patched.py` |
| Entropy scoring of context anchors | ✅ Implemented | `entropy.py:35-69` |
| Dynamic context expansion for low-entropy regions | ✅ Implemented | `entropy.py:131-223` |
| Cook-time uniqueness verification (whole-binary search) | ✅ Implemented | `entropy.py:90-128`, `recipe_builder.py:533-557` |
| Per-instruction anchor quality metadata in recipe | ✅ Implemented | `recipe_builder.py:102-104` (Change dataclass) |
| Force Save escape hatch (bypass uniqueness guard) | ✅ Implemented | `server.py:161`, `client.rs:281-298`, `save_tune.rs:237-266` |
| Architecture-aware masked matching (YARA-style) | ❌ Missing | — |
| Fuzzy/confidence-based matching | ❌ Missing | — |
| Structural/landmark-based anchoring | ❌ Missing | — |
| Section-boundary-aware context capture | ❌ Missing | — |
| Post-patch checksum recalculation | ❌ Missing | — |
| Low-entropy anchor scanner in annotator | ✅ Implemented | `annotator.py:162-238` |
| Enhanced anchor: ctx + ob + ctx_after | ✅ Implemented | `patcher.py:184-185` |
| Patcher failure diagnostics (anchor composition, ob-in-file check) | ✅ Implemented | `patcher.py:242-290` |
| Checksum-change scanner in annotator | ❌ Missing | — |
| IMMO-data scanner in annotator | ❌ Missing | — |

---

## 6. Recommended Mitigations (Priority-Ordered)

### ✅ Priority 1: Entropy-Gated Context Expansion During `cook` — IMPLEMENTED

**Effort: LOW** | **Impact: HIGH** | **Addresses: Risk 4.1**

Before writing a recipe, measure the Shannon entropy of each instruction's 32-byte `ctx`. If below a threshold, expand the window (64 → 128 → 256 bytes) until the anchor is statistically unique within the binary. Store the actual context size per instruction in the recipe.

This is the highest-leverage fix — it eliminates the collision risk without requiring any architecture-specific knowledge.

**Status (2026-05-30):** Implemented in `entropy.py` (`find_unique_context`), integrated into `recipe_builder.py` via `_get_verified_context()` and Guard 3 in `build_recipe()`. New recipe fields: `ctx_entropy`, `ctx_unique`, `ctx_expanded`, `context_size`.

### ✅ Priority 2: Minimum Uniqueness Verification — IMPLEMENTED

**Effort: LOW** | **Impact: HIGH** | **Addresses: Risk 4.1**

During `cook`, for each instruction: search the entire original binary for the `ctx + ob` pattern. If it appears more than once, the anchor is ambiguous even in the *same* binary — it will be worse in a different SW revision. Flag the instruction and auto-expand context until the pattern is unique.

**Status (2026-05-30):** Implemented as part of Priority 1. `find_unique_context()` searches the entire original binary for `ctx+ob` at each expansion step. Both conditions (entropy ≥ threshold AND match_count == 1) must be satisfied. `require_unique` flag controls hard-stop vs. warning behavior.

### Priority 3: Fill the Annotator Pipeline

**Effort: MEDIUM** | **Impact: MEDIUM** | **Addresses: Risk 4.4**

Implement the missing scanners as `InstructionScanner` plugins:
1. Low-entropy context flagger (uses the same entropy measurement from Priority 1)
2. Checksum region detector (pattern-match common checksum locations by ECU family)
3. IMMO block detector

The plugin architecture already exists — these are additive, non-breaking changes.

### ⏸️ Priority 4: Architecture-Aware Masked Matching — DEFERRED

**Effort: HIGH** | **Impact: HIGH** | **Addresses: Risks 4.2, 4.3**

Leverage the manufacturer extractors (`openremap/core/manufacturers/`) to provide per-architecture masks for the context window. For known ECU families (Bosch EDC17, Infineon TriCore, etc.), identify which byte positions in the context are opcodes (stable) vs. operands (volatile). The patcher then performs a masked comparison — operand bytes are wildcarded.

This requires per-architecture knowledge and is a significant engineering investment, but it's the proper long-term solution for cross-revision compatibility.

**Status (2026-05-30): Deferred.** Risk profile is real but the implementation surface is large. Masking must be conservative — wildcarding the wrong bytes can cause silent writes to wrong addresses (worse than today's safe-fail behavior). Requires per-architecture mini-disassembler (TriCore instruction decoding, C167 operand bitfields, etc.). Will be revisited when cross-revision apply failures become a demonstrated pain point with real-world data.

### Priority 5: Post-Patch Checksum Recalculation

**Effort: MEDIUM** | **Impact: MEDIUM** | **Addresses: Risk 4.5**

Implement checksum recalculation for known ECU families. The manufacturer extractors already identify the ECU type — this knowledge can drive a checksum calculator that runs after patching, automatically correcting checksum bytes before the binary is returned.

---

## 7. Phase 2 Deep-Dive: The Patcher Anchor Search

### 7.1 How It Works Now

Phase 2 is the `ECUPatcher` (`patcher.py`) — the engine that actually writes modified bytes into a target binary using context anchors to find the right location.

**The core algorithm** (`_find`, lines 174-204):

```python
anchor = ctx + ob              # atomic search pattern
win_start = max(0, expected - 2048)
win_end = min(len(snapshot), expected + 2048 + len(anchor))
region = snapshot[win_start:win_end]

# Exact byte matching only
matches = []
pos = 0
while True:
    p = region.find(anchor, pos)
    if p == -1: break
    matches.append(win_start + p + len(ctx))  # offset where ob starts
    pos = p + 1

# Tiebreaker: closest to expected offset
return min(matches, key=lambda o: abs(o - expected)), len(matches)
```

**Key properties:**
- Searches a ±2048 byte window around the expected offset
- Matches against a frozen **snapshot** — earlier writes never corrupt later searches
- `match_count > 1` = ambiguous (multiple hits), but the write is **still applied** — just picks the closest
- `match_count == 0` = instruction fails hard, entire patch is discarded
- When `ctx` is empty, falls back to direct read at the exact expected offset (no search)

**What improved with the entropy expansion (Priorities 1-2):** The `ctx` bytes are now verified unique in the *original* binary at cook time. This means in the common case (same binary, same revision), `match_count` will be exactly 1. But if the target binary differs (different SW revision, different padding layout), the anchor may still be ambiguous or missing — the ±2048 window and exact-match-only algorithm are unchanged.

### 7.2 How It Will Work After Priority 4 (Masked Matching)

Priority 4 proposes **architecture-aware masked matching** — the biggest remaining change to Phase 2:

**Current:** Every byte in `ctx + ob` must match exactly.
**Proposed:** The patcher receives a **mask** alongside each instruction, derived from per-ECU-family knowledge.

```
ctx:    0x48 0xA3 0x00 0x20 0x91 0x00 0x20 0xFF ...
mask:   0xFF 0xFF 0x00 0xFF 0xFF 0x00 0xFF 0x00 ...
        ^^^^^^^^^^^^^^^^     ^^^^^^^^^^^^^^^^     ^^^
        opcode bytes         operand bytes        padding/data
        (must match)         (wildcarded)         (wildcarded)
```

For TriCore/EDC17 ECUs, the manufacturer extractor knows:
- Which byte positions in a typical calibration section are opcodes (stable across revisions)
- Which are absolute addresses, offsets, or immediates (volatile — change on recompile)
- Which are padding/fill bytes (meaningless — always wildcard)

The patcher's `_find` would change from `snapshot.find(anchor)` to a masked scan:

```python
def _find_masked(self, ctx, ob, mask, expected):
    pattern = ctx + ob
    pattern_mask = mask + bytes([0xFF] * len(ob))  # ob always exact
    # Scan window same as today, but compare with mask
    for pos in range(win_start, win_end - len(pattern)):
        if all((pattern[i] & pattern_mask[i]) == (snapshot[pos+i] & pattern_mask[i])
               for i in range(len(pattern))):
            matches.append(pos + len(ctx))
    # ... same tiebreaker
```

This dramatically improves cross-revision compatibility — a recompile that changes a pointer offset in the context no longer breaks the anchor.

### 7.3 How It Will Work After Priority 4 + Structural Anchoring

Beyond masked matching, **structural anchoring** would change the patcher from a linear byte search to a two-phase approach:

1. **Landmark phase:** Find a stable structural feature near the target (e.g., a known axis pattern, a function prologue, a table header) using the manufacturer's knowledge of ECU memory layout
2. **Fine phase:** Use the masked context anchor relative to the landmark

This eliminates the spatial ±2048 window entirely — the landmark provides an absolute reference, and the context anchor provides sub-100-byte precision.

### 7.4 How It Will Work After All Proposed Changes (End State)

```
Target ROM + Recipe (with per-instruction: ctx, ob, mb, mask, landmark_hint, context_size)
       │
       ▼
┌──────────────────────────────────────────────┐
│ Phase 2: ECUPatcher (future)                  │
│   for each instruction:                       │
│     1. If landmark_hint: resolve landmark     │
│        → narrow search to ±64 bytes           │
│     2. Masked scan with ±2048 window          │
│        (or ±64 if landmark resolved)           │
│     3. Match scoring:                         │
│        - Exact mask match: score 1.0          │
│        - Partial mask match: score 0.7–0.9    │
│        - Best score > threshold → apply       │
│        - No match > threshold → fail          │
│     4. Write mb at best match offset          │
│   → all applied or ABORT                      │
└──────────────────────────────────────────────┘
```

---

## 8. Implications for openremap-studio

### 8.1 What Already Flows to the UI

The cook→studio pipeline already carries per-instruction anchor quality metadata. Here's the data flow:

```
Python cook()                         Rust deserialization             UI usage
─────────────                        ────────────────────            ────────
Change.ctx_entropy          ──JSON──► CookInstruction.ctx_entropy    (stored, not yet
Change.ctx_unique_in_original ──JSON──► CookInstruction.ctx_unique     rendered in UI)
Change.ctx_expanded          ──JSON──► CookInstruction.ctx_expanded
Change.context_size          ──JSON──► CookInstruction.context_size
```

These flow through:
1. `client.rs:297` — `serde_json::from_value(result)` deserializes the full `CookResult`
2. `workspace.rs:4218-4221` — maps to `Instruction` fields in `.orst` format
3. `orst.rs:93-102` — `Instruction` struct stores `context_size`, `ctx_entropy`, `ctx_unique`, `ctx_expanded`
4. `remap.rs:178-181` — round-trips through `.oremap` ZIP container

The **cook_warnings** from Guard 3 (non-unique anchors) are already surfaced in the **Build panel** (`build_view.rs:982-1152`) as a yellow warning block showing each warning message.

The **Force Save** button in the Save Tune dialog (`save_tune.rs:237-266`) is the escape hatch — it sets `require_unique=False`, which turns Guard 3 from a hard error into warnings.

### 8.2 What's Not Yet Surfaced (Immediate Opportunities)

| Data Available | Could Power | Effort |
|---|---|---|
| `ctx_entropy` per instruction | Hex view could tint low-entropy anchor regions (padding = red tint) | Low |
| `ctx_expanded` per instruction | Instruction list could show ⚠ badge on expanded-context instructions | Low |
| `ctx_unique` per instruction | Dashboard "anchor quality" score: % of instructions with unique anchors | Low |
| `context_size` per instruction | Tooltip: "Anchor: 128 bytes (expanded from 32)" | Low |
| `cook_warnings` list | Problems panel (already wired but placeholder) | Medium |

### 8.3 Future Studio Changes for Priority 4 (Masked Matching)

When masked matching lands, the recipe schema gets a new per-instruction field: `ctx_mask` (hex string, same length as `ctx`). Studio needs:

1. **`orst.rs`**: Add `ctx_mask: Option<String>` to `Instruction` struct (backward-compat with `#[serde(default)]`)
2. **`remap.rs`**: Thread `ctx_mask` through the conversion layer
3. **`types.rs`**: Add `ctx_mask: Option<String>` to `CookInstruction`
4. **Hex view**: When rendering a context anchor region, overlay the mask — dim wildcarded bytes, highlight opcode bytes
5. **Instruction list**: Add a "Mask quality" column showing what fraction of the anchor is wildcarded (lower = more stable)

### 8.4 Future Studio Changes for Priority 5 (Checksum Recalculation)

1. **New RPC method**: `recalc_checksum` → Python handler that dispatches to the manufacturer-specific checksum calculator
2. **Build dialog**: After building a recipe, offer "Recalculate Checksums" as a post-processing step
3. **Instruction flags**: Checksum instructions detected by the annotator get a `CHECKSUM_SUSPECT` flag — the instruction list can show a "Skip" button that removes them from the recipe

---

## 9. Key Files Reference

| File | Purpose |
|---|---|
| `openremap/core/services/recipe_builder.py` | Diff engine, context capture, recipe assembly |
| `openremap/core/services/patcher.py` | Anchor search (`_find`), instruction application |
| `openremap/core/services/validate_strict.py` | Phase 1: exact-offset pre-flight validation |
| `openremap/core/services/validate_exists.py` | Diagnostic: whole-binary `ob` search (EXACT/SHIFTED/MISSING) |
| `openremap/core/services/validate_patched.py` | Phase 3: post-patch mb verification |
| `openremap/core/services/annotator.py` | Instruction flagging framework (+ VIN scanner) |
| `openremap/core/services/identifier.py` | ECU identification dispatcher |
| `openremap/core/manufacturers/base.py` | `build_match_key()`, `check_match_key()` base implementations |
| `openremap/core/schemas/patcher.py` | `PatcherWarningsSchema` (API response model) |
| `openremap/cli/commands/tune.py` | CLI orchestration of the 3-phase pipeline |
