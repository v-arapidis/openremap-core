# `families` — command summary (fast-lookup)

> One-file reference for `openremap families [--family NAME]`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap families [--family NAME | -f NAME]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="families", ...)`
  → `openremap/core/cli/commands/families.py::families()`.
- Option `--family`/`-f`: accepts the family name or any known alias (e.g. EDC16,
  ME7, M3x, mp3.2, edc3, lh-jetronic), case-insensitive.
- **No `--json`**, no file argument, no service-layer calls — a pure display command
  over embedded data (`_FAMILIES`, 15 entries).

## Flow (top → bottom)

1. **No `--family`** → `_print_table()`: blank line + `Supported ECU Families`
   header + 73-col separator, per-family row (`FAMILY` cyan / `ERA` / `SIZE` /
   `NOTES`), closing separator, then two hint lines (`openremap families --family
   <NAME>`, `openremap identify <FILE>`).  Exit 0.
2. **With `--family`** → `_lookup(family)`:
   - `_normalize(name)` — lowercase + strip ` ` `-` `_` `.` `/` — the matching key.
   - Linear scan of `_FAMILIES` comparing the normalized name and every alias
     (`_FUZZY_CHOICES` / `_FUZZY_NEEDLES` — name+alias→entry pairs and plain needles —
     built once at import).
3. **No match** → red error to stderr (`Error: unknown family '<X>'.`), then a fuzzy
   "did you mean" via **rapidfuzz**: `process.extract(_normalize(family),
   _FUZZY_NEEDLES, limit=8, score_cutoff=50)` → up to **5 distinct** family names
   with match % (`<name> — NN%`), then `raise typer.Exit(code=1)`.
4. **Match** → `_print_detail(entry)`: cyan name header, `Era`, `File size`,
   `Sub-families` (one per line), `Identifier`, `SW format`, `HW in binary`,
   `Vehicles`, word-wrapped `Notes` (56-char wrap), closing hint lines.  Exit 0.

## Expected output

**Human** (no `--family`):

```
  Supported ECU Families
  ─────────────────────────────────────────────────────────────────────
    FAMILY                ERA          SIZE            NOTES
  ─────────────────────────────────────────────────────────────────────
    EDC1 / EDC2           1990–1997    32–64 KB        Audi 80 / A6 TDI, BMW 525 TDS, ...
    ...
```

**Human** (`--family EDC16`): full detail block — Era, File size, Sub-families
(EDC16C8 … EDC16U31), Identifier (`\xDE\xCA\xFE` DECAFE magic at bank-boundary
offsets), SW format, HW in binary, Vehicles, Notes.  No JSON variant exists.

**Exit codes:** `0` ok · `1` unknown `--family` value.

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `_FAMILIES` (embedded data) | `cli/commands/families.py` | display only — **no other code consumer**; must be kept in sync with the `manufacturers/` extractors (family names/aliases, SW formats) and `summary/commands/identify.md`'s family list |
| `rapidfuzz.process.extract` | third-party (`rapidfuzz`) | fuzzy "did you mean" only — no other CLI consumer of the normalized needle list |

## Gotchas

- The family table is **embedded documentation, not generated from the extractor
  registry** — a new extractor/family must be added to `_FAMILIES` manually, and the
  `identifier` / `sw_format` / `hw_in_bin` fields must match what the extractor
  actually does (heuristic layer, "never verified names").
- Matching is deliberately loose (`_normalize` strips case and separators) so `M3x`
  and `mp3.2` both resolve — but a typo can silently match an unintended family
  instead of erroring; the rapidfuzz suggestions only appear after an
  exact-normalized miss.
- No `--json` — scripted consumers of family data must parse the human table or read
  the source module.
