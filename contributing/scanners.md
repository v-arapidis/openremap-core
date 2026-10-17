# Contributing: instruction scanners

> Paths in this guide are relative to the repository root.

When a recipe is cooked, every changed instruction can be inspected and
**flagged** — "this rewrite touches a VIN", "this one sits in low-entropy
context and may not be portable". Flags are advisory: they never change the
patch, they tell a human where to look. A scanner is one such inspection rule.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first.

## Where things live

`openremap/core/services/recipes/annotator.py`:

| Piece | What it is |
|---|---|
| `InstructionScanner` | A `Protocol`: implement `scan(instruction, original_data) -> List[InstructionFlag]` |
| `InstructionFlag` | Frozen dataclass: `kind`, `confidence`, `reason` (plus `to_dict()`) |
| `VINScanner`, `LowEntropyScanner` | The two scanners that ship |
| `RecipeAnnotator` | Runs the scanners; `add_scanner()` registers another |

The annotator is wired into the pipeline: `recipe_builder.py` constructs a
`RecipeAnnotator` and calls `annotate(recipe, original_data)` at the end of a
cook, which populates `recipe["instructions"][i]["flags"]`.

## Adding a scanner

```python
class MyScanner:
    def prepare(self, original_data: bytes) -> None:   # optional
        """Called once before scanning — precompute what every scan needs."""

    def scan(self, instruction: Dict, original_data: bytes) -> List[InstructionFlag]:
        ...
        return [InstructionFlag(kind="MY_KIND", confidence=0.8, reason="…")]
```

Then add it to the default list in `RecipeAnnotator.__init__` — that is what
makes it part of every cook, and it is the in-tree extension point (the
`Protocol` is duck-typed, so no base class is required).

Guidance that keeps a scanner useful:

- **`prepare()` for anything expensive.** It runs once; `scan()` runs per
  instruction, on binaries that can be megabytes.
- **One reason per flag, in plain words.** The reason string is what a tuner
  reads in the report; "low entropy context (12 distinct bytes / 32)" beats
  "suspicious".
- **Confidence is a number you must be able to defend.** Some behaviours are
  near-certain (a VIN in the rewritten bytes), others are hints. Do not flatten
  them to the same value.
- **Advisory only.** A scanner never aborts a cook, never modifies bytes and
  never decides portability on its own — `cook-volatile` does that, with its own
  evidence trail.
- **Keep the flag kind stable.** Downstream consumers key on `kind`; renaming it
  is a breaking change and needs a changelog entry.

## What a PR must show

- A test in `tests/tuning/test_annotator.py` covering: flag fires on the
  positive case, does **not** fire on a near-miss, and `prepare()` state is
  reusable across instructions.
- A sentence on the false-positive risk: what else in a normal dump could look
  like your pattern.
- The exact `kind` string and confidence value you chose, so they can be
  reviewed as a contract rather than as an implementation detail.
