# Recipe schema 5.0 — specification

**Status:** design agreed 2026-10-19. **No producer emits 5.0 yet.** The
current engine still writes 4.3 / 4.4 / 4.5 and is frozen until the new
apply engine and the new cook are verified against it.

**Scope rule (2026-10-19):** the new apply commands read **5.0 only**. Legacy
4.x recipes stay with the deprecated `openremap tune`, which already handles
them. Splitting the product this way removes every "derive the contract from a
stamp and absent sections" guess from the new engine — that guessing is exactly
what it was built to eliminate.

**Scope:** the `.remap` recipe document. Not the `.orst` editor format.

> New code described below lives in the apply package under
> openremap/core/services/recipes/apply/ and ships in the same change.
> That path is deliberately written without backticks: the doc-path guard
> resolves backticked repo paths against the *committed* tree, so naming a
> not-yet-committed path that way would make this spec fail its own guard
> depending on commit order.

---

## 1. Why a new major version

4.x stores how much a recipe can be trusted in one overloaded string:
`metadata.portability: "same_file_only"`. That single value conflates two
independent facts — **whether the anchors are unique** and **what identity
level an apply command needs**. Everything downstream has to guess which one
it is looking at, and the guess is where the confusing checks came from.

5.0 replaces that with an explicit `contract` block. Four of its five fields
are **re-derivable from the payload**, so a consumer can detect a doctored or
stale contract instead of trusting it.

The 5.0 reading of `schema_version`: the number says which **envelope** a
consumer must understand. Optional sections live in `contract.sections`, not
in the version number. A future additive section is therefore still 5.x.

---

## 2. Top-level shape

```json
{
  "type": "recipe",
  "schema_version": "5.0",
  "contract": { … },
  "producer": { … },
  "source": { … },
  "tune": { … },
  "fingerprint": "sha256:…",
  "author": { … },
  "metadata": { … },
  "statistics": { … },
  "instructions": [ … ],
  "maps": [ … ],
  "regions": { … }
}
```

Required: `type`, `schema_version`, `contract`, `producer`, `source`, `tune`,
`fingerprint`, `instructions`. Optional: `maps`, `regions`, `author`,
`metadata`, `statistics`.

**Nothing about checksums is stored anywhere in the recipe.** Not regions, not
flags, not "touched" booleans. A field without a consumer can only go stale or
lie, and a wrong stored region is the one failure that silently drops real tune
bytes — the single failure direction that is neither visible nor recoverable.
Checksum knowledge is instead a *live* capability over a binary
(`openremap/core/services/checksums/`), never data carried from the donor's
dump.

---

## 3. `contract` (required) — the heart of 5.0

| field | type | meaning | re-derivable? |
|---|---|---|---|
| `identity_floor` | `"any"` \| `"firmware"` \| `"file"` | the **minimum** identity an apply command must verify | yes — from `anchors_unique` |
| `anchors_unique` | bool | every instruction's `ctx + ob` anchor occurs exactly once in the source | yes — `all(inst.ctx_unique)` |
| `sections` | list\[str\] | which optional top-level sections are present, sorted | yes — from presence |

### `identity_floor` derivation

* all anchors unique → `"any"` — the byte match is self-verifying.
* at least one anchor non-unique → `"firmware"` — position cannot be
  distinguished by bytes alone, so identity has to carry the guarantee.
* `"file"` is reserved for a producer that demands byte identity. Nothing
  emits it today.

The floor is a **minimum**: a command may always be stricter. A command may
never be looser than the floor.

## 4. `producer` (required)

```json
{ "engine": "openremap", "version": "0.8.0", "command": "cook",
  "created_at": "2026-10-19T09:53:00Z", "warnings": ["…"] }
```

`command` is the cook command that produced the file (`cook`,
`cook-volatile`, or a future cook policy). `warnings` replaces 4.x
`metadata.cook_warnings` — they describe the cook, so they belong to the
producer, not to the user metadata.

---

## 5. `source` and `tune` (required)

```json
"source": {
  "filename": "orig.bin", "size": 4194304, "sha256": "…",
  "identity": {
    "match_key": "EDC17C66::1037541778126241V0", "manufacturer": "Bosch",
    "ecu_family": "EDC17", "ecu_variant": "…", "software_version": "…",
    "hardware_number": "…", "oem_part_number": "…", "calibration_id": "…",
    "confidence": "HIGH"
  }
},
"tune": { "filename": "stage1.bin", "size": 4194304, "sha256": "…" }
```

`source.identity` is the **full** identify result, not the lean subset 4.x
keeps. It costs nothing: the cook already identifies both binaries for its
identity guard (measured ~768 ms per 4 MB image), and apply needs one
identification to fill the whole report.

`tune.sha256` is **new** — 4.x records only the tuned file's name and size,
which cannot detect a swapped tuned binary.

**5.0 drops the `ecu` block.** `source.identity` is the single source of
truth. Interop consequence, stated plainly: the deprecated 4.x commands read
`ecu.file_size` / `ecu.match_key` / `ecu.sha256`, so on a 5.0 recipe their
*advisory* checks (size, match-key warning, same-file-only gate) silently go
quiet. Their mechanical safety — exact-offset `ob` validation and the
anchored search — is unaffected, because `instructions` is unchanged.

---

## 6. `fingerprint` (required) — unchanged

`sha256:` over the ordered `(offset, ob, mb)` tuples. Independent of
annotation, so adding/removing sections never changes it. The audit service
re-cooks and compares; that stays exactly as it is.

---

## 7. `instructions` (required)

Same shape as 4.x, split by class:

| class | fields | rule |
|---|---|---|
| **semantic** | `offset` (int), `size` (int), `ob` (hex, upper), `mb` (hex, upper), `ctx` (hex, upper), `context_after` (hex, upper) | missing or unknown → **refuse** |
| **annotation** | `offset_hex`, `ctx_entropy`, `ctx_unique`, `ctx_expanded`, `description`, `flags`, `region` | ignored, reported |
| **new** | `needs_review` (bool) | reserved: set when an exclusion could only be applied partially — see §10 |

`size` is redundant with `ob`/`mb` lengths and is kept because it is used as
a fast guard everywhere.

---

## 8. Optional sections

### `maps`
Unchanged from 4.4 (structural descriptors + axes + `instruction_refs` +
probabilistic `label`/`label_confidence`). Purely informational; the apply
engine ignores it.

### `regions` (new in 5.0)
```json
"regions": { "tagged": 79, "risky": 41,
             "by_region": { "calibration": 38, "mixed": 40, "erased": 1 } }
```
Today the cook computes this and throws it away. Stored, it lets apply say
"40 of 79 changes sit outside calibration regions" without rescanning.

## 9. Reader rules

1. `type` must be `"recipe"`; `schema_version` major must be `5`. A `4.x`
   recipe is refused with a pointer to `openremap tune`; a `6.x` recipe is
   refused as "newer format, upgrade".
2. A top-level section not listed in `contract.sections` and not in the known
   set → **refuse** (an unknown section may change meaning).
3. An instruction field outside the semantic + annotation sets → **refuse**.
4. Contract self-consistency: re-derive `anchors_unique` and `sections`; any
   disagreement → **refuse** (doctored or stale).

## 10. Commitments (bind 5.0 producers and the apply engine)

1. **Exclusion requires full containment.** A change may be excluded only
   when every one of its bytes lies inside a store range. A partial overlap —
   a merged instruction up to 1024 bytes wide — is **kept** and marked
   `needs_review`, never dropped.
2. **No silent loss.** An invariant test asserts kept bytes + excluded bytes
   equal the diff blocks.
3. **A field nobody reads is a field that can only lie.** That is why checksum
   data is not in the recipe at all (§2), and why the contract carries three
   fields, each with a consumer.
4. **Exclusion, when it ever exists, lives only in cook** and requires *full
   containment*: a partial overlap is kept and marked `needs_review`, never
   dropped. The v1 cook excludes nothing.
5. **No auto-repair.** Recomputing a checksum for a target is a separate
   capability over a binary, out of scope for both the recipe and apply.
6. **Schema freeze.** 5.0 → 6.0 only for a semantic change: the meaning of an
   existing field changes, or a semantic field is removed or renamed.
   Everything additive stays 5.x.

The two failure directions are not equal, and that is why these rules exist:
a wrong checksum is visible and fixable; a silently dropped piece of the tune
is neither.

---

## 11. Not in 5.0 (reserved)

| Reserved | Would be |
|---|---|
| revision-independent instruction locators (map fingerprint + row/col) | major bump |
| checksum repair instructions | major bump (changes the meaning of applied bytes) |
| signing / provenance signatures | additive if optional |

---

## 12. Producer obligations and measured costs

On a 4 MB image:

| step | cost | today |
|---|---|---|
| `identify` × 2 (stock + tune) | ~1540 ms | already paid by cook's identity guard |
| `sha256` × 2 | ~5 ms | partly paid |
| region summary → stored instead of discarded | ~0 | free |
| full `source.identity` instead of the lean subset | ~0 | free |
