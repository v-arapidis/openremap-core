---
title: cook — advanced
description: Compare stock vs tuned and save a recipe — every flag, output fields, examples.
---

# `openremap cook`

Compare a stock (unmodified) ECU binary and a tuned ECU binary. Every byte
that changed between them is recorded — its offset, original value (`ob`),
new value (`mb`), and a short context anchor (`ctx`) that is used to find
the right location even if the offset has shifted slightly in a different
software revision.

The result is saved as a recipe file with the `.remap` extension. That recipe
is the input for all `validate` and `tune` commands.

---

## Usage

```bash
openremap cook <ORIGINAL> <MODIFIED> [OPTIONS]
```

---

## Arguments

| Argument | Required | Description |
|---|---|---|
| `ORIGINAL` | Yes | The unmodified (stock) ECU binary. Must end in `.bin` or `.ori`. |
| `MODIFIED` | Yes | The tuned ECU binary. Must end in `.bin` or `.ori`. |

The order matters — `ORIGINAL` always comes first, `MODIFIED` second.

---

## Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--output PATH` | `-o` | stdout | File path to write the recipe JSON to. If omitted, the recipe is printed to the screen. |
| `--context-size N` | `-c` | `32` | Number of bytes of context to capture before each changed block (8–128). A larger value gives the patcher a better anchor when offsets have shifted. |
| `--pretty / --compact` | | `--pretty` | Pretty-print the JSON with indentation, or write it as a single compact line. |
| `--annotate-maps / --no-annotate-maps` | | `--annotate-maps` | Annotate the recipe with a schema 4.4 `maps` layer (map descriptors + instruction refs). On by default; `--no-annotate-maps` emits the lean 4.3 format. |
| `--allow-non-unique` | | off | Produce the recipe even when context anchors repeat in the stock binary (recipe reliable only on this exact binary). |
| `--help` | | | Show help and exit. |

---

## Examples

```bash
# Cook a recipe and save it to a file
openremap cook stock.bin stage1.bin --output recipe.remap

# Cook and print the recipe to the screen (useful for inspection)
openremap cook stock.bin stage1.bin

# Use a wider context window — recommended when you plan to apply the recipe
# to ECUs that may be on a slightly different software revision
openremap cook stock.bin stage1.bin --context-size 64 --output recipe.remap

# Compact output — smaller file, harder to read
openremap cook stock.bin stage1.bin --compact --output recipe.remap
```

---

## Example output

```
  Cooking recipe from stock.bin vs stage1.bin …

  ✅ Recipe built successfully

  ECU                    Bosch · EDC17
  Match Key              EDC17::08001505827522B
  Format Version         4.4
  Instructions           277
  Bytes Changed          43,577
  Original               stock.bin
  Modified               stage1.bin

  Recipe saved to recipe.remap
```

---

## What to look for

| Field | What it tells you |
|---|---|
| **ECU** | Manufacturer and family extracted from the original binary. Should match what `openremap identify` reported. |
| **Match Key** | The identity string embedded in the recipe. Any target binary you apply this recipe to must have the same match key, or `validate before` will warn you. |
| **Instructions** | Number of changed byte blocks found. Zero means the two files are identical — check you passed the right files in the right order. |
| **Bytes Changed** | Total number of bytes that differ. Gives a rough sense of how large the tune is. |

### Good result

- `Instructions` is greater than zero.
- `ECU` shows a recognised manufacturer and family (not `Unknown`).
- `Match Key` is populated — a missing match key means recipes built from
  this binary cannot be matched to target ECUs reliably.

### Needs attention

- **Zero instructions** — the two files are identical. Make sure you passed
  the stock binary first and the tuned binary second, and that you have the
  right files.
- **`ECU` shows `Unknown`** — the original binary's ECU family is not yet
  supported. The recipe is still built correctly (all byte differences are
  captured), but there will be no match key, which means `validate before`
  and `tune` will not be able to confirm the target is the right ECU.
- **A read error** — check that both file paths exist and that both files
  end in `.bin` or `.ori`.

---

## How it works

`cook` reads both files entirely into memory, then walks through them byte
by byte to find every position where they differ. Consecutive changed bytes
are grouped into a single instruction block. For each block it records:

- `offset` — where in the binary the change starts
- `ob` — the original bytes (hex-encoded)
- `mb` — the modified bytes (hex-encoded)
- `ctx` — a short slice of bytes immediately before the change, used as an
  anchor during tuning to locate the block even if the offset has shifted

The ECU identity (`manufacturer`, `family`, `match_key`, etc.) is extracted
from the original binary using the same extractor registry that powers
`openremap identify`.

The full recipe format is documented in [`docs/recipe-format.md`](../../concepts/recipe-format.md).

---

## Notes

- `cook` is completely read-only with respect to its inputs. Neither the
  original nor the modified binary is ever changed.
- Both files must be the same size. If they differ in length, `cook` will
  exit with an error — size differences indicate a fundamentally different
  binary layout that the byte-diff approach cannot handle.
- The recipe JSON is human-readable. Open it in any text editor to inspect
  exactly what the tune changes before applying it to anything.

---

## See also

- [Recipe format spec](../../concepts/recipe-format.md) — full field reference for `.remap` files
- [Tune command](../tune/index.md) — one-shot validate → apply → verify
- [Validate command](../validate/index.md) — individual validation steps

