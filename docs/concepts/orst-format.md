# Tune Format (.orst)

> **⚠ DEPRECATED.** The `.orst` saved-tune format is being phased out.
> New work should use the portable [`.remap` recipe format](recipe-format.md)
> (schema 4.4), which now covers the saved-tune use case with the
> calibration-map annotation layer. Existing `.orst` files remain
> readable, but the format will not receive new features.

The `.orst` (OpenRemap Saved Tune) format is the workspace-native file that
stores a saved tune. It carries the same instruction shape as a `.remap`
recipe but with minimal metadata — just enough for the editor to reopen,
display history, and export as a portable recipe.

This format is consumed by **openremap-studio**. It is not exposed through
the CLI or TUI.

---

## Format Version

The current format version is **2.0**, recorded in the `orst` field.

| Version | Changes |
|---|---|
| 1.0 | Initial .orst format (Rust-only, had `description` and `author` fields) |
| 2.0 | Schema defined in Python library. Dropped `description` and `author`. Added `message`, `base_tune_id`, `archived_at`, `instructions[].status`. |

**Parsers MUST ignore unknown fields.**

---

## Top-level Structure

```json
{
  "orst": "2.0",
  "id": "orst_a1b2c3d4...",
  "name": "Stage 1",
  "message": "Increased idle timing by 2°",
  "source_binary": {
    "sha256": "3a7bd3e2360a...",
    "file_size": 524288,
    "path_hint": "stock.bin"
  },
  "base_tune_id": null,
  "created_at": "2026-06-02T12:00:00Z",
  "modified_at": "2026-06-02T14:30:00Z",
  "archived_at": null,
  "instructions": [ ]
}
```

| Field | Type | Description |
|---|---|---|
| `orst` | `string` | Schema version — `"2.0"` |
| `id` | `string` | Stable tune ID (`orst_<32hex>`). Survives renames |
| `name` | `string` | Display name, user-editable |
| `message` | `string \| null` | Commit message from Ctrl+S, max ~120 chars |
| `source_binary` | `object` | Identity of the stock binary this tune was cooked against |
| `base_tune_id` | `string \| null` | The tune this one was forked from. `null` = derived from stock |
| `created_at` | `string` | ISO 8601 UTC — when the tune was first created |
| `modified_at` | `string` | ISO 8601 UTC — last Ctrl+S |
| `archived_at` | `string \| null` | Set when this is an archive snapshot; `null` for the live version |
| `instructions` | `array` | Changed byte ranges — same shape as recipe instructions |

### `source_binary`

| Field | Type | Description |
|---|---|---|
| `sha256` | `string` | SHA-256 of the stock binary |
| `file_size` | `integer` | Size of the stock binary in bytes |
| `path_hint` | `string` | Last-known filename; not authoritative |

### `instructions`

Same field shape as `.remap` recipe instructions, with one additional field:

| Extra field | Type | Description |
|---|---|---|
| `status` | `string` | `"Normal"` (default) or `"Unresolved"` (ob not found after binary rebase) |

All other fields (`offset`, `ob`, `mb`, `ctx`, `context_after`, `context_size`,
`ctx_entropy`, `ctx_unique`, `ctx_expanded`, `description`, `flags`) are
identical to the [recipe instruction format](recipe-format.md#instructions).

---

## Relationship to .remap

```
Stock.bin + Modified buffer
        │
        ▼
  ECUDiffAnalyzer
        │
        ├── build_recipe()  →  .remap  (full: ecu, statistics, creator, ...)
        │
        └── build_orst()    →  .orst   (minimal: instructions + source + name)
```

Exporting a recipe from a tune is a direct field copy of instructions —
the instruction shape is identical between formats. The `.orst` just omits
the ECU identity, statistics, creator provenance, and compatibility metadata
that are irrelevant to the editor workflow.

---

## See also

- [Recipe Format](recipe-format.md) — the portable `.remap` format
- [Architecture](architecture.md) — how the Python library and Rust studio interact
