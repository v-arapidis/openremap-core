# Contributing: map hunting & scoring

> Paths in this guide are relative to the repository root.

Map hunting is how OpenRemap finds calibration tables without a definition file:
it looks for **strictly increasing 16-bit sequences** (axis breakpoints) and
promotes the rectangular block that follows a pair of them into a table
candidate, scored heuristically. Everything a contributor touches here is a
trade-off between finding real maps and inventing fake ones.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first.

## Where things live

| Piece | Location |
|---|---|
| **The scorer** (edit here) | `openremap/_rs/src/maps/map_hunter.rs` |
| Python dispatch | `openremap/core/services/maps/map_hunter.py` (`scan_map_axes`, `scan_map_tables`) |
| Layout segmenter | `openremap/core/services/maps/layout.py` |
| Content labels | `openremap/core/services/maps/map_classifier.py` |
| Cross-reference evidence | `openremap/core/services/maps/xrefs.py` |
| Map-level diff | `openremap/core/services/maps/diff_maps.py` |
| CSV export | `openremap/core/services/maps/map_exporter.py` |

`map_hunter.py` is a thin adapter — the axes/tables scan is one Rust call. If you
find yourself adding scoring logic in Python, it belongs in the Rust file.

## Rebuilding after a Rust change

```bash
uv run --with maturin maturin develop --release
```

Without it, the tests keep exercising the *previous* extension — a silent
half-hour of debugging. Rebuild first, then test.

## What the hunter needs from a table (and why attacks fail)

The layout it recognises is the common WinOLS/DAMOS one:

```
[ X axis (cols × 2 B) | Y axis (rows × 2 B) | cells (cols × rows × cell_width) ]
```

Constraints worth knowing before you "improve" detection, all observed on
generated fixtures with `openremap synth` (see [tests.md](tests.md)):

- axes are **strictly increasing u16**, length 4–32, step 1–10000;
- **the shape of the cell surface decides detectability more than the shape of
  the table.** A smooth *monotonic* surface (rising in both axes, like a torque
  map) is detected exactly at every size; an **oscillating** surface (sine-like)
  is missed outright or truncated, because its local extrema read as competing
  structure. A quantised/plateaued surface is likewise unreliable.
- the Y axis can be **truncated** by the re-scan heuristic (a 16×12 table with
  8-bit cells is reported as 16×10). Before "fixing" such a case, reproduce it
  on a real binary — synthetic fixtures make the behaviour visible, they do not
  decide whether it is wrong.

## Review bar for a scoring change

1. **Before/after on the local corpus.** Run identification plus `scan-maps`
   over `tests/data/ECUs` and diff: which files gained or lost tables, and by
   how much. "It finds more maps" is not a result; "these 12 files gained a
   table each, these 3 lost a low-score candidate, no identity changed" is.
2. **Expect the golden corpus to move.** `tests/corpus/` pins detected
   candidates and their scores on synthetic dumps, so an intentional scoring
   change *will* fail it. Regenerate with `_dev/corpus/update_golden.py`, **read
   the diff**, and include it in the PR with a sentence explaining each moved
   number. Never regenerate to silence a failure you have not understood.
3. **Cost.** The scan runs on multi-megabyte binaries; if your change adds an
   inner-loop pass, measure it (`_dev/benchmarks/` has the harnesses) and say
   what it costs.
4. **Determinism.** Same input ⇒ same candidates and scores, on any machine.
   Avoid float accumulation order changes that make scores jitter in the last
   digits — a pinned score that drifts is a false regression signal.

## Thresholds are API too

`min_axis_length`, `max_axis_length`, `min_step`, `max_step`, `min_score`,
`max_series_tables` and friends are part of the public surface (declared in the
API registry schema, surfaced through `list_methods`). If you add or rename one,
declare it in the schema, keep the CLI flag in step, and update the docs — an
undeclared parameter is invisible to every consumer.
