# Recipe Format

A recipe is a JSON file with the `.remap` extension that captures every byte-level change between an original and a modified ECU binary. It is the central data structure of the entire OpenRemap pipeline — produced by `openremap cook`, consumed by every validate and patch command.

Recipes are fully portable between the CLI and the API. A recipe cooked on the command line can be applied through the API and vice versa.

---

## Format Version

The current format version is **4.3**, recorded in `schema_version` at the top level.

The version is checked during validation and patching. A recipe with an unrecognised format version will be rejected before any instructions are read.

Version history:
| Version | Changes |
|---|---|
| 4.0 | Initial recipe format |
| 4.1 | Added `creator`, `fingerprint`, `trust_level` |
| 4.2 | Entropy-gated context expansion: `ctx_entropy`, `ctx_unique`, `ctx_expanded`, `max_context_size`, `cook_warnings`, instruction `flags` |
| 4.3 | Flattened `openremap` envelope; added `source` and `application`; `creator` restructured to human-only; new `metadata` fields (`name`, `tags`, `instruction_count`); `flags.confidence` normalized to float; `statistics.context_size` renamed to `min_context_size`; `ecu` block extended with `oem_part_number`, `platform`, `calibration_version`, `serial_number`, `dataset_number` |

**Parsers MUST ignore unknown top-level fields.** New fields may be added in minor versions without breaking existing consumers.

---

## Top-level Structure

```json
{
  "type": "recipe",
  "schema_version": "4.3",
  "source": "full_cook" | "tune_export",
  "application": "openremap-core" | "openremap-studio",
  "creator": { },
  "fingerprint": "",
  "metadata": { },
  "ecu": { },
  "statistics": { },
  "instructions": [ ]
}
```

| Field | Type | Description |
|---|---|---|
| `type` | `string` | Always `"recipe"` |
| `schema_version` | `string` | Format version — currently `"4.3"` |
| `source` | `string` | How the recipe was produced: `"full_cook"` (binary diff) or `"tune_export"` (exported from a saved tune) |
| `application` | `string` | Which application produced the file: `"openremap-core"` (CLI / TUI) or `"openremap-studio"` (desktop app) |
| `creator` | `object` | Human author identity — see below |
| `fingerprint` | `string` | SHA-256 of instruction content for dedup and corruption detection |
| `metadata` | `object` | Information about the source binaries |
| `ecu` | `object` | ECU identity the recipe was built for |
| `statistics` | `object` | Aggregate diff summary |
| `instructions` | `array` | Per-byte-block change instructions |

---

## `creator`

Human author identity. No tool fields — tool provenance is carried by `application` and `schema_version`.

| Field | Type | Description |
|---|---|---|
| `name` | `string` | Display name. Empty string = anonymous |
| `handle` | `string` | Optional handle (GitHub, Discord, etc.) |
| `id` | `string` | Optional stable user ID for provenance |
| `created_at` | `string` | ISO 8601 UTC — when the human created this |
| `signature` | `string \| null` | Future: cryptographic signature over the fingerprint |
| `trust_level` | `string` | `UNSIGNED`, `COMMUNITY`, `SIGNED`, or `VERIFIED` (future enforcement) |

```json
"creator": {
  "name": "pinx",
  "handle": "pinx",
  "id": "",
  "created_at": "2026-06-02T14:30:00Z",
  "signature": null,
  "trust_level": "COMMUNITY"
}
```

---

## `fingerprint`

SHA-256 hash of `(offset, ob, mb)` tuples sorted by offset. Used for deduplication and corruption detection — same tune produces the same fingerprint regardless of metadata. Not tamper protection on its own; will be combined with `creator.signature` for cryptographic verification in the future.

```json
"fingerprint": "sha256:00f727e8abf62d384acc4420b08fe8e5477f9d004c8d3a697bbaaa08fe2149f5"
```

---

## `metadata`

Information about the files the recipe was built from.

| Field | Type | Description |
|---|---|---|
| `name` | `string` | Short human-readable label |
| `description` | `string` | Freeform longer explanation |
| `tags` | `string[]` | Categorization tags, e.g. `["stage1", "egr-off"]` |
| `instruction_count` | `integer` | Number of instructions in the recipe (mirrors `statistics.total_changes`) |
| `original_file` | `string` | Filename of the unmodified (stock) binary |
| `modified_file` | `string` | Filename of the tuned binary, or tune display label for `tune_export` |
| `original_size` | `integer` | Size of the original binary in bytes |
| `modified_size` | `integer` | Size of the modified binary in bytes |
| `tune_id` | `string \| null` | Source tune ID (`orst_<32hex>`) for `tune_export`. `null` for `full_cook` |

```json
"metadata": {
  "name": "Stage 1 — ME7.5",
  "description": "Increased idle timing, raised rev limiter",
  "tags": ["stage1", "rev-limiter"],
  "instruction_count": 42,
  "original_file": "stock.bin",
  "modified_file": "stage1_tune.bin",
  "original_size": 524288,
  "modified_size": 524288,
  "tune_id": null
}
```

---

## `ecu`

The identity of the ECU the recipe was built for. Every validation and patch operation checks this block against the target binary before touching a single byte.

All string fields are `null` when the identifier could not be extracted from the binary. New extractors added in future library versions will populate fields that were previously `null` without a schema change.

| Field | Type | Description |
|---|---|---|
| `manufacturer` | `string \| null` | ECU manufacturer (e.g. `"Bosch"`) |
| `match_key` | `string \| null` | Compound identity key — see below |
| `ecu_family` | `string \| null` | ECU family (e.g. `"ME7.5"`, `"EDC17"`) |
| `ecu_variant` | `string \| null` | Variant within the family |
| `software_version` | `string \| null` | Software version string |
| `hardware_number` | `string \| null` | Hardware part number |
| `calibration_id` | `string \| null` | Calibration identifier |
| `oem_part_number` | `string \| null` | OEM part number from binary headers |
| `platform` | `string \| null` | Platform (e.g. `"VAG"`, `"BMW"`, `"PSA"`) |
| `calibration_version` | `string \| null` | Calibration revision within the dataset |
| `serial_number` | `string \| null` | ECU serial number |
| `dataset_number` | `string \| null` | Dataset / flash index |
| `file_size` | `integer` | Exact byte size of the original binary |
| `sha256` | `string` | SHA-256 hash of the original binary |
| `cook_warnings` | `string[]` | Non-fatal warnings produced during cooking |

```json
"ecu": {
  "manufacturer": "Bosch",
  "match_key": "me7.5__0261208592__367276",
  "ecu_family": "ME7.5",
  "ecu_variant": null,
  "software_version": "0261208592",
  "hardware_number": "0261208592",
  "calibration_id": "367276",
  "oem_part_number": "06A906032HJ",
  "platform": "VAG",
  "calibration_version": "0003",
  "serial_number": null,
  "dataset_number": null,
  "file_size": 524288,
  "sha256": "3a7bd3e2360a3f5c8d4e1b9f0a2c6d7e8b3f1a4e5c9d2b6f0e7a1c3d5e9b2f4",
  "cook_warnings": []
}
```

### The `match_key`

`match_key` is the primary compatibility gate. It is a compound string in the form `FAMILY::VERSION` and is compared against the target binary's own `match_key` before any validation or patching begins.

How it is built depends on the ECU architecture:

| Case | `match_key` form | Example |
|---|---|---|
| Normal ECU with software version | `FAMILY::SOFTWARE_VERSION` | `ME7.5::1037354003` |
| LH-Jetronic Format A (no SW version by design) | `FAMILY::CALIBRATION_ID` | `LH-JETRONIC::1012621LH241RP` |
| Unknown or anonymised binary | `null` | — |

If the target binary's `match_key` does not match the recipe's `match_key`, the operation is rejected immediately with a clear mismatch message. A `null` `match_key` in the recipe disables this check and falls through to byte-level validation.

### `cook_warnings`

Non-fatal issues detected during cooking (`full_cook` only; always empty for `tune_export`):
- Identity mismatches between original and modified binaries
- Non-unique context anchors (when Force Save bypasses the uniqueness guard)
- Low-entropy context regions

These are surfaced in the UI so the user can review them before applying the recipe.

---

## `statistics`

A summary of the diff. Informational only — not used during patching.

| Field | Type | Description |
|---|---|---|
| `total_changes` | `integer` | Number of instructions in the recipe |
| `total_bytes_changed` | `integer` | Total number of bytes that differ between the two binaries |
| `percentage_changed` | `float` | Percentage of the binary that changed |
| `single_byte_changes` | `integer` | Number of 1-byte instructions |
| `multi_byte_changes` | `integer` | Number of multi-byte instructions |
| `largest_change_size` | `integer` | Size of the largest instruction in bytes |
| `smallest_change_size` | `integer` | Size of the smallest instruction in bytes |
| `min_context_size` | `integer` | Configured minimum context anchor size (renamed from `context_size` in 4.2) |
| `max_context_size` | `integer` | Maximum context anchor size after auto-expansion |

```json
"statistics": {
  "total_changes": 42,
  "total_bytes_changed": 168,
  "percentage_changed": 0.032,
  "single_byte_changes": 10,
  "multi_byte_changes": 32,
  "largest_change_size": 8,
  "smallest_change_size": 1,
  "min_context_size": 32,
  "max_context_size": 512
}
```

---

## `instructions`

An array of patch instructions. Each instruction describes one contiguous block of bytes that differs between the original and modified binary.

```json
"instructions": [
  {
    "offset": 6699,
    "offset_hex": "1A2B",
    "size": 4,
    "ob": "AABBCCDD",
    "mb": "11223344",
    "ctx": "DEADBEEF112233445566778899AABBCCDDEEFF00112233445566778899AABBCC",
    "context_after": "CAFEBABE112233445566778899AABBCCDDEEFF00112233445566778899AABBCC",
    "context_size": 32,
    "ctx_entropy": 5.2,
    "ctx_unique": true,
    "ctx_expanded": false,
    "description": "Idle timing base offset",
    "flags": []
  }
]
```

### Instruction fields

| Field | Type | Description |
|---|---|---|
| `offset` | `integer` | Absolute byte offset of the change in the binary |
| `offset_hex` | `string` | Same offset in uppercase hex, without `0x` prefix |
| `size` | `integer` | Number of bytes in this instruction |
| `ob` | `string` | **Original bytes** — uppercase hex. What must be present at `offset` before patching |
| `mb` | `string` | **Modified bytes** — uppercase hex. What is written when patching |
| `ctx` | `string` | Context window of `context_size` bytes immediately **before** the change — used as an anchor |
| `context_after` | `string` | Context window of `context_size` bytes immediately **after** the change |
| `context_size` | `integer` | Actual length of `ctx` in bytes (may be larger than `min_context_size` due to auto-expansion) |
| `ctx_entropy` | `float \| null` | Shannon entropy of `ctx` in bits/byte (0.0 = uniform, 8.0 = random) |
| `ctx_unique` | `boolean \| null` | `true` when the `ctx + ob` pattern is unique in the original binary |
| `ctx_expanded` | `boolean \| null` | `true` when the context was auto-expanded beyond `min_context_size` |
| `description` | `string` | Human-readable summary of the instruction |
| `flags` | `array` | Annotator flags attached to this instruction |

### Instruction flags

Each flag is an object with:

| Field | Type | Description |
|---|---|---|
| `kind` | `string` | Flag type: `VIN_SUSPECT`, `CHECKSUM_SUSPECT`, `LOW_ENTROPY_CTX` |
| `reason` | `string` | Human-readable explanation of why this instruction was flagged |
| `confidence` | `float` | Confidence score 0.0–1.0 (was string `HIGH`/`MEDIUM`/`LOW` in 4.2) |
| `action` | `string` | `WARN`, `SKIP`, or `REVIEW` |

### `ob` and `mb`

All byte strings are uppercase hex with no separators. A 4-byte value is represented as 8 hex characters: `"AABBCCDD"`.

`ob` (original bytes) is what the strict validator checks before patching. If the bytes at `offset` do not match `ob` exactly, the instruction fails validation and the patch is rejected.

`mb` (modified bytes) is what the patcher writes. The post-patch validator checks that `mb` is present at `offset` after the patch is applied.

### `ctx` — the anchor

`ctx` is a window of bytes immediately preceding the changed block in the **original** binary. The default minimum size is 32 bytes (`min_context_size`), but the cook pipeline uses **entropy-gated geometric expansion** to ensure each anchor is both high-entropy and unique within the original binary:

1. Start with `min_context_size` bytes (default 32).
2. Compute Shannon entropy of the candidate context.
3. Search the entire original binary for `ctx + ob`.
4. If entropy < 2.5 bits/byte OR the pattern appears more than once, double the context size (64 → 128 → 256 → 512).
5. Repeat until both conditions are met, or `max_context_size` is reached.
6. If `max_context_size` is reached and the anchor is still non-unique: when `require_unique=True` (default), raise a hard error listing the failed instructions. When `require_unique=False` (Force Save), record the non-unique anchors as `cook_warnings` and proceed.

Quality metadata (`ctx_entropy`, `ctx_unique`, `ctx_expanded`) is stored per-instruction for downstream tooling.

### `context_after`

`context_after` is the same-size window of bytes immediately **after** the changed block. During patching, it is appended to the search anchor — the patcher searches for `ctx + ob + context_after` (not just `ctx + ob`). This doubles the effective anchor length at zero cost.

The anchor search works as follows:
1. Read `ob` bytes at the exact `offset` recorded in the instruction.
2. If they match — apply the patch immediately.
3. If they do not match — search the region `[offset - 2048, offset + 2048]` for the pattern `ctx + ob + context_after`.
4. If found at a new offset — apply the patch at the shifted position.
5. If not found — the instruction fails with a diagnostic message.

---

## `source` — full_cook vs tune_export

| Field | `full_cook` | `tune_export` |
|---|---|---|
| How produced | Binary diff: stock vs modified (Python `ECUDiffAnalyzer`) | Field copy from a saved `.orst` tune |
| Stock binary needed | Yes, at cook time | No |
| `ecu.*` fields | Full from `identify_ecu()` | From cached identity + `.source_binary` |
| `metadata.tune_id` | `null` | `orst_<32hex>` |
| `cook_warnings` | Populated from diff guards | Empty |
| `creator` | From CLI / TUI user config | From app user profile |

---

## Safety Properties

- **No blind writes.** The patcher never writes `mb` without first confirming `ob` is present — either at the recorded offset or at a shifted position found via the anchor search.
- **All-or-nothing validation.** The strict validator checks every instruction before the patcher writes a single byte. A single failure aborts the entire operation.
- **Identity gate.** `match_key` and `file_size` are verified against the target binary before any instruction is read.
- **Portable.** Recipes contain no absolute paths, no machine-specific data, and no binary blobs. They are plain JSON and can be stored, versioned, shared, and diffed.

---

## See also

- [Cook command](commands/cook.md) — how to create a recipe from two binaries
- [Tune command](commands/tune.md) — one-shot validate → apply → verify
- [Validate command](commands/validate.md) — individual validation steps
- [About OpenRemap](about.md) — project overview and use cases

---

← [Back to documentation](README.md)
