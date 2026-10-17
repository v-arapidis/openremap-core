# Contributing: ECU extractors

> Paths in this guide are relative to the repository root.

**This is the single most impactful contribution.** The whole pipeline —
identification, recipe building, validation, patching — is manufacturer-agnostic.
Adding an extractor makes all of it work for a new family automatically.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first: the safety notice there is
binding for everything below.

## The fast path: let the wizard measure your dump

Writing the extractor is the easy part. The hard part is knowing **which bytes
are stable in your family and absent from every family the engine already
recognises** — get that wrong and your extractor silently steals binaries that
another family used to identify correctly. You cannot check it by reading your
own file.

```bash
openremap contribute extractor stock.bin --also tuned.bin --brand Siemens --family SID206
openremap contribute extractor stock.bin --also tuned.bin --brand Siemens --family SID206 --write
```

Run it **from your clone of this repository**. The collision check uses the
committed fixtures in `_dev/contrib/recipes/`, so from a plain `pip install` the
default refuses outright and tells you to clone; add `--measure-only` for a first
look that needs no checkout — it reports who claims your dump, the runs stable
across your dumps and the family name your dump suggests, but **not** collisions,
so it recommends no marker and writes nothing. Contributing means opening a pull
request, so you need the clone anyway.

The first form only measures and prints: who claims your dump today, ranked
candidate signatures (each one *tested against every known family*), the
collisions with offsets, and candidate identity fields split into "constant in
your family" (belongs in `can_handle()`) and "varies per dump" (what a
`match_key` needs). The second form additionally writes four files:

| file | what it is |
|---|---|
| `_dev/contrib/recipes/<brand>-<family>.json` | your family as fill byte + planted payloads — the fixture that goes in the PR **instead of your dump** |
| `openremap/core/manufacturers/<brand>/<family>/extractor.py` | a skeleton that already passes the review bar below, with the measured marker, size gate and exclusion guards |
| `tests/tuning/manufacturers/test_<family>_extractor.py` | the fixture-driven test, runnable with no ECU data |
| `<family>-contribution.md` | the PR description, including a **Not verified** section |

Before calling anything verified, the wizard **executes the skeleton it just
generated** against every known-family fixture: it must claim your fixture and
steal nobody else's. If your marker collides, the wizard derives an exclusion
guard from the colliding family's own ident token and re-checks — and if no
guard can work (your marker *is* their token), it exits non-zero instead of
producing a file that looks done.

Give it **two or more dumps of the same family** (`--also`). With one dump, a
byte that simply varies between calibrations is indistinguishable from a family
constant, and the report says so rather than pretending.

If you only want to know whether your ECU is already recognised, that needs no
clone at all — and no family name:

```bash
openremap identify your.bin
openremap contribute extractor your.bin --also second_dump.bin --measure-only
```

The second command is the wizard's first step: it reports what the engine thinks
your dump is, the byte runs that are stable across your dumps, and the family
name your dump itself suggests — which is usually the name you would have had to
guess to run the full version.

Then, before opening the PR:

```bash
.venv/bin/python -m pytest tests/tuning/manufacturers/test_<family>_extractor.py
openremap contribute check openremap/core/manufacturers/<brand>/<family>/extractor.py
```

`contribute check` is the same bar a reviewer applies: it claims its own fixture,
it steals no other family's, every `return False` clears the evidence,
`supported_families` matches what `extract()` reports, and the extractor is
actually reachable from the registry. It is also what to run after any later
edit to an extractor.

Three things the wizard deliberately does **not** do: it does not register the
extractor for you (position is behaviour — that is the judgement call), it does
not fill in the identity fields (only your dumps can decide those), and it does
not claim your extractor is *correct* — a fixture built from your own marker
proves self-consistency, not that real ECUs of this family look like that.

## How the extractor system works

Every extractor lives in `openremap/core/manufacturers/<brand>/<family>/extractor.py`
and subclasses `BaseManufacturerExtractor`. When a binary is submitted, the
registry calls `can_handle()` on each extractor **in priority order** and
delegates all extraction to the **first** one that returns `True`.

The base class (`openremap/core/manufacturers/base.py`) is well documented. Read
it before you start — it explains `can_handle()`, `extract()`,
`build_match_key()`, the evidence tags, and the opt-in fallback mechanism.

## Step-by-step

**1. Create the package directory**

```
openremap/core/manufacturers/<brand>/<family>/
├── __init__.py      (empty)
├── extractor.py     (your implementation)
└── patterns.py      (regex patterns and search regions — optional but recommended)
```

For example, a Siemens SID206 extractor would live at
`openremap/core/manufacturers/siemens/sid206/extractor.py`.

**2. Implement the extractor**

```python
from openremap.core.manufacturers.base import (
    BaseManufacturerExtractor,
    EXCLUSION_CLEAR,
    FAMILY_STRING,
    SIZE_MATCH,
)
from typing import Dict, List

class SiemensSID206Extractor(BaseManufacturerExtractor):

    @property
    def name(self) -> str:
        return "Siemens"

    @property
    def supported_families(self) -> List[str]:
        return ["SID206"]

    def can_handle(self, data: bytes) -> bool:
        # Fast, bounded check only — this is called on every binary.
        if len(data) not in (0x40000, 0x80000):   # ← your family's known sizes
            self._set_evidence()                  # ALWAYS clear on every False
            return False
        if b"OTHER_FAMILY_TAG" in data[:0x10000]:  # exclusion guard
            self._set_evidence()
            return False
        if b"SID206" not in data[:0x10000]:
            self._set_evidence()
            return False
        # Evidence tags are how the confidence scorer knows *why* you matched:
        # more (and stronger) tags ⇒ higher reported confidence.
        self._set_evidence([SIZE_MATCH, EXCLUSION_CLEAR, FAMILY_STRING])
        return True

    def extract(self, data: bytes, filename: str = "unknown.bin") -> Dict:
        # Required keys: file_size, md5, sha256_first_64kb
        import hashlib
        return {
            "manufacturer": self.name,
            "file_size": len(data),
            "md5": hashlib.md5(data).hexdigest(),
            "sha256_first_64kb": hashlib.sha256(data[:0x10000]).hexdigest(),
            "ecu_family": "SID206",
            "ecu_variant": None,
            "software_version": None,   # extract from the binary
            "hardware_number": None,
            "calibration_id": None,
            "match_key": self.build_match_key(
                ecu_family="SID206",
                software_version=None,  # pass what you extracted
            ),
        }
```

**3. Register it in the brand's `EXTRACTORS` list**

Open `openremap/core/manufacturers/<brand>/__init__.py` (create it for a new
brand) and insert your extractor **in the right position**, most specific first:

```python
from openremap.core.manufacturers.<brand>.<family>.extractor import <Brand><Family>Extractor
from openremap.core.manufacturers.base import BaseManufacturerExtractor

EXTRACTORS: list[BaseManufacturerExtractor] = [
    <Brand><Family>Extractor(),
]
```

**4. Register the brand in the top-level registry** (new brands only)

Open `openremap/core/manufacturers/__init__.py` and unpack your brand into
`BUILTIN_EXTRACTORS`:

```python
from openremap.core.manufacturers import bosch, siemens  # add your brand here

BUILTIN_EXTRACTORS: list[BaseManufacturerExtractor] = [
    *bosch.EXTRACTORS,
    *siemens.EXTRACTORS,   # add your brand here
]
```

**5. Verify it works**

```bash
uv run openremap identify your_binary.bin
uv run openremap cook stock.bin modified.bin --output recipe.remap
```

## Priority order is the risky part — treat it as your main design decision

Detection is **first match wins**, so position *is* behaviour: an extractor
placed too early silently takes binaries away from the family that used to
claim them.

- Place yours **after** every built-in it could overlap with, unless the two are
  provably disjoint.
- When you do have to sit between two families, **write the reason as a comment
  in the list** — that comment is the only place a future reader learns why the
  order matters. The existing ME7.9.x entry is the pattern to copy:

  ```python
  # BoschME79Extractor — ME7.9.x ...: ident dialect "ZZ<<" at 0x10000 differs
  # from the ME7 extractor's "ZZ<digit>" guard, so the two are disjoint.
  # Must run AFTER BoschME7Extractor (ME7 keeps its existing claims) and
  # BEFORE BoschME9Extractor (same size neighbourhood).
  BoschME79Extractor(),
  ```
- **Prove you did not steal claims.** Run the identification sweep before and
  after your change over the local `tests/data/ECUs` corpus and diff the
  results: your two dumps should be the *only* files whose identity changed. If
  you have no corpus of your own, say so in the PR — the maintainer will run it
  — and never ship an extractor that changes other families' results silently.

## Tips for a good `can_handle()`

- **Keep it fast.** It runs on every binary, for every extractor. Scan bounded
  regions (`data[:0x10000]`) rather than the whole file.
- **Be exclusive, not just inclusive.** If your family shares signatures with
  another, add explicit guards that reject the other family — see
  `BoschExtractor.can_handle()` for layered exclusion guards.
- **File size is a strong discriminator.** Many older families have fixed sizes
  (32 KB, 64 KB, 256 KB…). Use it as a cheap pre-filter.
- **Collect evidence.** Tags are what turn "it matched" into a confidence tier;
  missing tags make a correct detection look weak.

## Tips for a good `extract()`

- **Extract `software_version` if at all possible.** It is the main component of
  `match_key`; without it, recipe matching falls back to a less reliable field.
- **Use `_run_all_patterns()` and `_search()`** from the base class — they
  handle region slicing, iteration, error suppression and deduplication.
- **Keep patterns in `patterns.py`** so the logic stays readable and the byte
  patterns can be updated independently.
- **Use `build_match_key()`** rather than formatting the key yourself: it
  normalises whitespace and applies the `ecu_variant` → `ecu_family` priority.

## Tests are part of the contribution

Add `tests/tuning/manufacturers/test_<family>_extractor.py` (the directory is
flat — one file per family, no brand sub-directory). Cover `True` for your
family's signatures, `False` for other families' bytes and for wrong sizes, and
the fields you extract. See [tests.md](tests.md) — and note that you do **not**
need an ECU dump to write them.
