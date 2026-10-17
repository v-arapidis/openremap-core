# Contributing: checksum profiles

> Paths in this guide are relative to the repository root.

Checksum work answers one question: **which checksum scheme does this binary
satisfy, and does it actually verify?** The engine will tell you a stored value
is stale — it will never correct it. Keeping that distinction is the whole point.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first — in particular "No Damos or
A2L Derived Logic" and "Original Heuristics Only".

## The two layers

| Layer | File | What it is |
|---|---|---|
| Generic sweep | `openremap/core/services/checksums/checksum.py` | A **closed, parameterised space**: algorithm family (`sum8/sum16/xor8/crc8/crc16ccitt/crc16arc/crc32ieee`, …) × init × final-XOR × region (whole file, 16/32/64 KB pages, tail exclusions) × store location (file end / page end) × direct or two's-complement. Brute-forced in Rust (`checksum_compute`). |
| Family detectors | `openremap/core/services/checksums/<family>.py` | Hand-written, structure-driven: they locate the *descriptor*, *table* or *routine* the family uses and parse it. |

Family detectors are registered by **explicit import** in
`openremap/core/services/checksums/report.py` — that file is the registry:

```python
from openremap.core.services.checksums.denso import detect_denso
from openremap.core.services.checksums.ironfelix import detect_all as detect_ironfelix
from openremap.core.services.checksums.ms43 import detect_ms43
from openremap.core.services.checksums.nefmoto import ...
```

Adding a profile means: write the detector module, export a `detect_<family>()`
that returns the same shape as its siblings, call it from `report.py`, and record
the scheme in the support matrix.

## Honest-by-construction — the rule that matters

A single match is **noise**: a random 8-bit store collides roughly 1 time in
256, and there are many parameter combinations, so accidental matches happen.
Evidence scales like this:

- a **per-page scheme matching ≥ 90 % of non-erased pages** is a real signal
  (a page counts as erased when ≥ 90 % of it is one repeated byte);
- **family consensus across many factory files is proof.** A scheme observed in
  one dump is a hypothesis, not a fact.

That last rule has teeth: if you have two dumps of a family, you have a
hypothesis. Say so in the PR rather than shipping a detector that will fire on
the wrong binaries.

## Playbook

1. **Find public ground truth for the family** — a community wiki, an
   open-source flashing project, published definitions. Never a Damos/A2L file,
   never a closed-source tool's behaviour (see the safety notice).
2. **Discover the scheme structurally** from real binaries you own: where the
   descriptor/table lives, which regions it covers, whether the end is inclusive,
   where the value is stored.
3. **Port the hot path to Rust** (`openremap/_rs/src/checksums/`) if it scans or
   brute-forces, and **parity-verify** Python against Rust on real files.
4. **Update the support matrix** in the checksum documentation so detection is
   gated correctly, and add a regression test.

## Invariants — do not "fix" these

- **Detection only.** Nothing here corrects a checksum.
- A scheme that fires on one file is reported with that caveat, not as truth.
- Checksum *stored values* are volatile bytes: their classification belongs to
  `openremap/core/services/recipes/volatile.py`, not here. The sweep is **not**
  an exclusion source for recipes (it is weak evidence); only a real family
  detector may report a store range.
- Some formats are honestly "stale" and stay that way (e.g. a runtime-patched
  entry). Documenting a known-stale case is a valid contribution.

## What a PR must show

- Which family, which file sizes, and how many dumps you verified against
  (consensus, not a single file).
- The exact structure you keyed on (offset/descriptor shape), so a reviewer can
  check it independently.
- The algorithm and the store location, plus whether the region end is
  inclusive.
- A statement about **false positives**: what you ran it against that did *not*
  match, ideally the local `tests/data/ECUs` corpus.
- A test with synthetic bytes that pins the detection, and — where the
  arithmetic allows it — a one-line property test of the checksum function
  itself.
