# `_dev/contrib/` — the contribution wizard's mimic corpus

**One consumer: the contribution wizard. Nothing else.**

This corpus exists so the wizard can answer a question a contributor cannot
answer alone: *"does my candidate signature also fire on a file that belongs to
a different family?"* For that it needs files the real extractors **accept** —
and real dumps cannot be committed. So we build them.

## Read this before trusting anything here

- It is **not** a test corpus: no test asserts *detection* behaviour on these
  files. The single exception is a bookkeeping guard
  (`tests/tuning/manufacturers/test_declared_families.py`), which reads only
  `manifest.json` — never the 44 MB of fixtures — to check that every family key
  an extractor *declares* is either reported by a fixture or listed as
  unreachable. It skips cleanly when the corpus is not checked out.
- It proves **nothing** about real ECUs. Every file is generated from a recipe
  we wrote by reading our own extractors, so it encodes *our assumptions*.
  Passing here means "we are self-consistent", never "we are correct".
- Real dumps remain the only oracle for whether the extractors are right.

## Layout

```
_dev/contrib/
├── build_corpus.py          deterministic builder + verifier (committed)
├── derive_recipes.py        derives per-extractor recipes from the extractors
├── derive_families.py       steers a base recipe to each declared family key
├── recipes/<mfr>-<family>.json   one recipe per family (committed)
└── corpus/<name>.mimic + manifest.json   the generated files + hashes (committed)

```

**Why `.mimic` and not `.bin`:** the repository gitignores `*.bin`
(`.gitignore:63` — "no ECU firmware, ever"). A `.bin` fixture would silently
never be committed, and committing one anyway would make the "no binaries in
this repository" rule read as a lie. Detection never looks at the extension
(`can_handle()` sees only bytes), so nothing is lost — do not rename these back.

Weight is a non-issue: the files are mostly fill bytes, so **2.2 MB of corpus
compresses to ~3 KB in git**. Even all 59 families would be tens of KB.

## Commands

```bash
.venv/bin/python _dev/contrib/build_corpus.py verify         # every recipe still identifies as expected
.venv/bin/python _dev/contrib/build_corpus.py coverage        # extractor + family-key coverage, measured
.venv/bin/python _dev/contrib/build_corpus.py build           # (re)write corpus/ + manifest.json
.venv/bin/python _dev/contrib/build_corpus.py build --check   # write nothing; fail on any drift
.venv/bin/python _dev/contrib/build_corpus.py search EDC15    # dev aid: brute-force a recipe
```

**`recipes/` is also the wizard's input.** `openremap contribute` loads these
recipes (through `openremap/core/services/wizard/fixtures.py`) and rebuilds the
fixtures in memory to answer "does this candidate marker also occur in another
family's file?" — there is **no copy of them inside the package**: an installed
wheel has no recipes, so the wizard measures but does not check collisions and
does not write artifacts. Same ritual as any other change to a recipe: run
`build`, review the manifest diff, commit.

`--check` is the guard that makes a tampered corpus visible: it compares the
files **on disk** against the manifest hashes *and* the manifest against the
recipes. Someone who hand-edits a corpus file — say, to make their own
signature look unique — must also edit the manifest and the recipes, and *that*
is what shows up in a review diff instead of an unreadable binary blob.

## A recipe

```json
{
  "manufacturer": "Bosch",
  "ecu_family": "EDC1",
  "file_size": 32768,
  "fill": "FF",
  "derived_from": {"snapshot": "_dev/oracle/smallfixes-snapshot/after/identify.jsonl",
                   "source_sha256": "c2fc…"},
  "plants": [
    {"at": "0x7FD9", "seq": [{"text": "0281001214"}, {"hex": "FF"}, {"text": "2537355342"}]}
  ],
  "expect": {"manufacturer": "Bosch", "ecu_family": "EDC1"}
}
```

- `at`: `0x7FD9` (absolute) or `end-0x40` (from the end).
- `seq` tokens: `{"text": …}` ASCII, `{"hex": …}` raw bytes, `{"run": "FF", "count": N}` repeats.
- `derived_from`: provenance — the strings come from the `raw_strings` that
  `identify` reported for a **real** file, referenced by its sha256. No binary
  is stored, and nothing personal is carried (the snapshot was checked: no VINs).
- `expect`: **manufacturer + ecu_family**, plus **ecu_variant when the recipe
  declares one.** The variant is gated only where it carries the whole
  distinction: Bosch EDC16 hardcodes `ecu_family="EDC16"` and reports
  C31/C34/CP33 as variants, so the family alone would gate nothing for those
  twelve fixtures. Remaining fields are deliberately *not* reproduced:
  rebuilding each family's real ident-block structure would buy the wizard
  nothing, so `match_key` is recorded in the manifest as `observed`, not gated
  on.

## Adding a family

1. `build_corpus.py search <FAMILY>` — brute-forces fill byte × separator ×
   offset × string order against one snapshot row and prints what worked.
2. Write the recipe using those parameters (and the row's `raw_strings`).
3. `build_corpus.py verify` → `build` → `coverage` → **read the diff** → commit.

## Measured state (2026-10-17)

**89 fixtures · 89/89 verify · 38 of 38 extractors · 89 of 104 declared family
keys.** 44 MB raw → **~68 KB gzipped** in git.

`build_corpus.py coverage` is what these numbers come from — it re-identifies
every fixture and prints the misses, so a claim here can always be re-measured
rather than believed. Two things it checks that nothing else does:

- **the claimer**, not just the family: a fixture counts only if the extractor
  that wins `can_handle()` is one that declares the key. Planting `ME7910` into
  a bare recipe also spells `ME7.`, so the ME7 extractor — earlier in the
  registry — claimed that file and *its* loose `ME\d+` regex reported `ME7910`.
  The key looked covered while the ME7.9.10 extractor had never seen the file.
- **undeclared families**: a fixture whose `ecu_family` no extractor declares
  (`A1.2`, produced by an early hand-written M5.x recipe) is a smell, and it is
  now printed instead of silently passing `verify`.

Both derivation tools treat **the extractor as the specification**:

- `derive_recipes.py` — introspects each extractor's module (constants,
  byte-string literals, `slice` offsets, and the bare hex offsets that only
  appear inside `data[...]`), searches size × fill × payload × anchor in
  cheapest-first phases, gates on "the intended extractor accepts **and** no
  earlier extractor claims it", then **minimises** the recipe.
- `derive_families.py` — one recipe per extractor is not family coverage: it
  takes the base recipe and **steers the ident payload to each declared family
  key**, verifying that `identify` then reports it (as `ecu_family` *or*
  `ecu_variant`, because some extractors carry the distinction in the variant).
  Six steering mechanisms, in the order they were needed:

  | mechanism | what it does | cracked |
  |---|---|---|
  | token substitution | rewrites the family-shaped token inside the base's plants | M1.3, M1.7, M3.3, MED9, MP9.0 |
  | marker harvest | plants a marker harvested from the extractor's own source | M1.8, SIMOS, SID801A |
  | regex instances | instantiates `rb'"0000000M2\.(\d)'` per digit | M2.8, M2.9 |
  | header magic | reads the byte comparisons (`data[0] == 0x22`, `data[1:5] == b"\xff…"`, `sz == _MAX_SIZE`) and plants them, grouped by branch | DME-3.2, EZK, M1.x-early |
  | file size | changes only `file_size` where size *is* the discriminator | SIMOS2, SIMOS3 |
  | minimal recipe | drops the base's plants entirely and plants `<marker><target>` | MP3.2, MP7.2 |

```bash
.venv/bin/python _dev/contrib/derive_recipes.py  --write   # per-extractor recipes
.venv/bin/python _dev/contrib/derive_families.py --write   # per-family recipes
.venv/bin/python _dev/contrib/derive_families.py --write --refresh   # re-pin expect of the derived ones
```

Coverage by increment: snapshot-string search 5/12 families → module-constant
introspection 17/38 extractors → code-derived anchors + marker assembly 24–28 →
multi-feature combinations 33 → hand-authoring the structural remainder 34 →
family steering 51/104 keys → **+6 mechanisms and hand-authoring → 89/104 keys,
38/38 extractors**.

### The 15 declared keys no fixture can cover

These are declared in `supported_families` but **no code path can ever return
them**, and no real dump produces them either (`coverage` marks that). Each is a
declaration that outlived the extractor's actual behaviour — worth a decision,
not more searching:

| key(s) | declared by | why it can never be reported |
|---|---|---|
| `EDC2` | `bosch.edc1` | `extract()` hardcodes `ecu_family="EDC1"`; EDC2 shares the layout but is never named |
| `EDC15C2`, `EDC15C3`, `EDC15C5`, `EDC15C7`, `EDC15M`, `EDC15VM+` | `bosch.edc15` | the extractor sets `ecu_family="EDC15"` and `ecu_variant=None` — by design: "always EDC15, no variant string in binary" |
| `M2.81` | `bosch.m2x` | the DAMOS fallback matches `/M2\.(\d+)/` and keeps `digits[0]`, so `M2.81` → `M2.8` |
| `M3.8.1`, `M3.8.3`, `M3.81`, `M3.82`, `M3.83` | `bosch.m5x` | `FAMILY_NORMALISATION` maps them onto `M3.8` |
| `M5.92` | `bosch.m5x` | same map: `M5.92` → `M5.9` |
| `ME7910` | `bosch.me79` | `extract()` hardcodes `ecu_family="ME7.9.10"`; `ME7910` is only the *detection* marker |

Two possible resolutions, both the operator's call: **narrow the declarations**
to what `extract()` can return (tests assert some of these strings — e.g.
`test_edc15_extractor.py::test_edc15c3_in_supported_families` — so the tests
would move with them), or keep them as "families this extractor understands"
and have the wizard treat the coverage gap as expected.

**Decision (2026-10-17): keep them.** `supported_families` is what the TUI
families browser lists, and someone with a Passat V5 `M3.8.3` searches for
exactly that string — narrowing the list would delete a useful entry to make a
metric look better, while the metric is the thing that was wrong: `104` was
never "labels the engine can print". The searchable number is **89 reportable
keys, 15 recognised-but-not-reported**. What the decision *did* buy is a guard,
so the list cannot quietly grow again:
`tests/tuning/manufacturers/test_declared_families.py` fails on a declared key
that has neither a fixture nor an entry in its `KNOWN_UNREPORTABLE` table (with
a reason and the declaring module), fails again if one of those keys *becomes*
reportable, and never reads the 44 MB of fixtures — only `manifest.json`.

