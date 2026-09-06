# Composite commands — classification criteria

The API layer has **three kinds** of methods:

| `kind` | Meaning | Defined as |
|---|---|---|
| `command` | **Atomic** — one cohesive operation, one domain | hand-written Python |
| `composite` | **Composition** — orchestrates independent operations/domains | hand-written Python |
| `workflow` | **Declarative pipeline** — linear, unconditional | data file (TOML), no code |

## Criterion — when is a hand-written command `composite`?

A command is **`composite`** when its non-presentation core orchestrates
**multiple independent operations** into one combined result. It must satisfy
**at least one** of:

1. **Cross-domain** — it calls service functions from **≥2 distinct service
   domains** (identify, maps, checksums, health, arch, recipes, entropy,
   convert) to build its result.
2. **Command composition** — it orchestrates **≥2 operations that are
   themselves exposed as separate API commands**.

A command is **`atomic`** when its core is **one cohesive operation in one
domain**, even if it:

- runs across a directory / many files (batch), or
- has internal sub-steps that are NOT exposed as separate commands, or
- uses cross-cutting low-level primitives (entropy, convert/decode) as helpers.

## The discriminator (quick test)

> **"If I deleted this command, would its sub-parts still be independently
> useful — as separate commands, or as separate service domains?"**
>
> - **YES** → `composite`.
> - **NO** (the whole thing is one atomic "do X") → `command` (atomic).

## Why composites are NOT workflows

Composites are hand-written because they have **control flow** (flags, gates,
fail-fast), **cross-result coherence** (identity ↔ checksum ↔ arch agree), or
**one-pass efficiency** that a linear declarative pipeline cannot express. A
`workflow` is reserved for simple, linear, unconditional compositions
(e.g. `identify_cook`).

## Classification (verified against source, 02-09-2026)

**Composite (6):**

| command | what it composes |
|---|---|
| `analyze` | identify + VIN + layout + maps + checksums + health + coherence |
| `tune` | validate_before → patch → validate_after |
| `cook` | diff + map scan + annotation + region tags |
| `cook_volatile` | cook + volatile classification |
| `diff_maps` | scan ×2 + map matching + cell diff |
| `health` | identity + checksums + maps + layout + VIN |

**Atomic (`kind="command"`):** `identify`, `scan`, `audit`, `merge`,
`scan_maps`, `convert`, `layout`, `scan_vins`, `routine`, `checksum`, `patch`,
`validate_before`, `validate_check`, `validate_after`, `scan_classify`, plus the
meta commands `ping` / `version` / `list_methods`.

> **Note:** `audit` and `merge` are **atomic**, not composite. Each is ONE
> service function in the recipes domain (`audit()`, `merge_recipes()`) whose
> sub-steps (provenance/fingerprint; validate-both/merge) are internal and not
> separately exposed. Do not re-classify them as composite.
