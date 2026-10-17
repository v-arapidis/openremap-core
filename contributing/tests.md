# Contributing: tests

> Paths in this guide are relative to the repository root.

Tests are the part of a contribution that survives review. This page covers
where they go, how to write them **without owning an ECU dump**, and the two
suites that behave differently from the rest.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first — especially "No Binary
Distribution".

## Layout

```
tests/
├── conftest.py            shared helpers (plain functions, not fixtures)
├── api/                   registry, transport, RPC contracts
├── cli/                   one file per command
├── checksum/  convert/  recipes/
├── corpus/                golden corpus over synthetic dumps (see below)
├── tuning/
│   ├── manufacturers/     one file per ECU family (flat — no brand sub-dirs)
│   └── services/          service-level behaviour
└── data/                  real dumps — GITIGNORED, local only
```

Run everything with `uv run pytest`. It is slow (several minutes plus the
corpus tests), so during development run what you touched:

```bash
uv run pytest tests/tuning/manufacturers/test_<family>_extractor.py -v
uv run pytest -k "can_handle"
```

## You do not need an ECU dump to write tests

Two sources of input, both safe to commit:

1. **Helpers** in `tests/conftest.py` — `make_bin()`, `make_bin_with()`,
   `make_layout_bin()`, `make_recipe()`, `make_instruction()`, `ctx_hex()`.
   Plain functions: import them, don't ask for them as fixtures.
2. **Generated dumps** — `openremap synth` builds an ECU-like binary from
   scratch (no OEM bytes anywhere) with a known ground truth:

   ```bash
   uv run openremap synth --output demo.bin --size 262144 --maps 6 --seed 42
   ```

   Deterministic: same parameters + seed ⇒ identical bytes, so you can pin a
   SHA-256 or commit the parameters instead of the file. Generated files carry
   no manufacturer fingerprints, so `identify` reports `Unknown` for them — by
   design, and useful as a negative case.

**Never commit a real dump, and never attach one to an issue or PR.** Firmware
is proprietary, and a dump can carry a VIN. If a test needs real data, guard it
(see below) — it will then run on machines that have the corpus and skip
everywhere else.

## Corpus-gated tests

Real-dump tests skip cleanly when `tests/data/` is absent, so CI stays green and
outside contributors are never blocked:

```python
DATA = Path(__file__).resolve().parents[2] / "tests" / "data"
HAS_SOMETHING = (DATA / "ECUs" / "Bosch" / "EDC17" / "…").is_file()

@pytest.mark.skipif(not HAS_SOMETHING, reason="corpus binary not present")
def test_something_on_real_data():
    ...
```

Use the same wording for the reason (`corpus binary not present`) — it is
greppable, and it tells a maintainer exactly why a test went quiet.

## The golden corpus (`tests/corpus/`)

Behavioural drift on synthetic dumps. Cases are **parameter sets** in
`tests/corpus/manifest.json`; the expected outputs live in
`tests/corpus/cases/<id>/golden.json` and pin identity, detected tables and
their scores, the cooked recipe, the re-patch hash, and both refusal layers for
a tampered recipe.

Rules if your change moves any of it:

- run `_dev/corpus/update_golden.py --check` to see *which* case and *which
  field* drifted;
- if the change is intentional, regenerate, **read `git diff tests/corpus`**, and
  commit the goldens together with the change that caused them;
- if it is not intentional, fix the code — the corpus just did its job.
- do not touch `manifest.json`'s `generator_version` unless you changed what
  `openremap synth` produces.

## Two suites that fail for non-obvious reasons

- **`tests/test_doc_paths.py`** — checks that committed living docs do not point
  at paths that no longer exist. If you move or delete a file, this is the test
  that tells you which document to update. Dated audits and *released*
  changelogs are exempt on purpose; the unreleased changelog is checked.
- **Anything importing the Rust extension** — if you changed `openremap/_rs/`,
  rebuild before testing (`uv run --with maturin maturin develop --release`),
  otherwise you are testing yesterday's binary.

## Style

- Plain `pytest` functions and classes; helpers come from `conftest.py` as
  functions, not fixtures.
- Assert the *behaviour*, not the implementation: what a user sees (identity
  fields, exit codes, JSON keys) rather than private internals.
- Per-family extractor tests are large by convention (positive signatures,
  negative cases, field extraction). That volume is deliberate — it is what
  makes a refactor safe.
