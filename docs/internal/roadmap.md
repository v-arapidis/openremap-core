---
title: Roadmap
description: Internal roadmap — what has shipped, what is open, and the suggested order for the next features.
---

# Roadmap (internal)

Repo-internal snapshot of the project state — what has shipped, what is
open, and the suggested order for the next features.  Self-contained:
maintenance notes live privately outside the repo, this page is the
public quick view.

**Current cycle:** [0.7.x roadmap](0.7.x-roadmap.md) — post-0.7.0
stabilisation (bug refinement + third-party OSS integration).

## Shipped

| Area | Status |
|---|---|
| Identify / confidence / evidence | ✅ |
| Recipe pipeline: cook, merge, tune, validate, audit | ✅ (`.remap` schema 4.5 — volatile-aware) |
| `cook-volatile` — car-portable recipes | ✅ schema-4.5 `volatile` section, volatile-aware audit (ISSUE-1 done) |
| Map tooling: scan-maps, diff-maps, classifier, CSV export | ✅ |
| Layout segmenter + ident blocks | ✅ |
| Checksums: ME7, IronFelix, NefMoto, MS43, GS20/SMG2, Denso | ✅ (see [checksum command docs](../commands/checksum/advanced.md)) |
| Health report (`openremap health`) | ✅ |
| Subaru support: Denso + Hitachi extractors, 501-ROM corpus | ✅ |
| Rust migrations (5 hot loops) + domain restructure | ✅ |

## Open

| Item | Notes |
|---|---|
| Diff-maps robustness | correlation matching, changed-axis handling |
| Layout consumers | scan-maps/diff-maps default region; cook region tags |
| Bundle convention | directory convention + `bundle.toml` |
| Subaru checksum gaps | 512 KB SH7058 / 2 MB SH72546 table discovery |
| Cross-firmware relocation | **0.8.0 milestone** — learn (stockA, tunedA) → apply to stockB of a different revision; community plugin tooling |
| Synthetic corpus generator | **0.8.0 milestone** — build reproducible real-like stock+tuned `.bin` fixtures from scratch (headers, axes, maps, checksums), so anyone can generate a synthetic corpus without real ROMs (replaces the private `tests/data/` + `benchmarks/`, which stay gitignored) |
| ISSUE-2 | Guard-3 strictness on non-unique anchors — decision |
| Rust migration candidates | diff-maps cell machinery (deferred) |
| Website manifest rework | deferred |

## Suggested order

1. Diff-maps robustness — the foundation for cross-firmware
   relocation (the long-term flagship)
2. Layout consumers
3. Subaru checksum gaps
4. Cross-firmware relocation + community plugin tooling (**0.8.0**)
5. Synthetic corpus generator — reproducible real-like fixtures for
   everyone (**0.8.0**)
6. Bundle convention
7. Modern TUI rework (**0.9.0**)
8. OpenRemap Harness — desktop app for Windows/macOS/Linux (**1.0.0**)

## Docs map

- `getting-started/` — install, quick start, CLI reference, TUI, setup
- `concepts/` — how it works, confidence, evidence, recipe format,
  architecture, orst format (deprecated)
- `commands/` — every command, two tiers (simple + advanced)
- `manufacturers/` — per-OEM, per-family pages
- `internal/` — repo-only: this roadmap, the 0.7.x roadmap,
  [dated audits](audits/), the deprecated server protocol
