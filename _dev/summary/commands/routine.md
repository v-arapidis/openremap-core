# `routine` — command summary (fast-lookup)

> One-file reference for `openremap routine <FILE> <OFFSET> [--arch …]
> [--before N] [--after N]`: entry point, exact call flow, expected output,
> and every shared function it touches (so a change to any of them can be
> checked against all consumers).  Keep this file updated when the command
> or its dependencies change.

## Entry & registration

- Command: `openremap routine <FILE> <OFFSET> [--arch ARCH] [--before N]
  [--after N]`
- Registered in `openremap/core/cli/main.py` via `app.command(name="routine")`
  → `openremap/core/cli/commands/routine.py::routine()`.
- `<OFFSET>` is a file offset: hex `0x…` or decimal (`int(text, 0)`).

## Flow (top → bottom)

1. **Parse offset** — `_parse_offset(text)` (`int(text, 0)`); bad input →
   `typer.BadParameter` (exit 2).
2. **Load binary** — `cli/io.py::load_binary_file(file, "Binary")`
   (HEX/SREC decode + raw passthrough).
3. **Resolve decoder** (only when `--arch` omitted):
   - `services/identify/identifier.py::identify_ecu(data, filename)` →
     manufacturer / family.
   - `core/arch/__init__.py::arch_for_family(manufacturer, family)` →
     arch tuple; `.arch[0]` = arch key.
   - No mapping → stderr hint ("pass --arch …") + exit 1.
4. **Render** — `core/arch/pseudocode.py::render_routine(data, offset,
   arch=key, before, after)` → `list[str]` lines:
   - `arch == "c166"` → `core/arch/c166.py::disasm` (Rust
     `c166_disasm`, mnemonic + operands).
   - else → capstone (`_render_capstone`): finds the code region via
     `services/maps/layout.py::segment` +
     `code_regions_from_layout`, disassembles a window, marks the target
     with `>>`.
5. **Print** — target line highlighted; others plain.

## Output shape

```
  <filename>  @ 0x<OFFSET>  (<arch-key>)

   04FFFA  RETS
>> 050000  MOV    [-R0], R9
   050002  MOV    [-R0], R8
```

## Shared dependencies → other consumers

| Function | Also used by |
|---|---|
| `arch_for_family` | `scan_maps`, `analyze`, `services/coherence.py`, `maps/xrefs.py`, the xref pass |
| `render_routine` / `pseudocode.py` | (new — no other consumers yet) |
| `c166.disasm` (Rust `c166_disasm`) | (new — the xref pass uses `c166_references`/`c166_walk`, not `disasm`) |
| `segment` / `code_regions_from_layout` | `scan_maps`, `analyze`, `maps/xrefs.py`, census script |
| `identify_ecu` | every identify/scan/analyze path |

## Notes / gotchas

- The decoder is a **phrasebook, not a decompiler** — no register renaming,
  dataflow, or loop reconstruction.
- C166 memory operands render as raw 16-bit DPP-windowed addresses; DPP
  resolution to flash addresses is a later refinement.
- `m680x` renders in 68HC11 mode by default (LH-Jetronic's 6800 is a close
  subset — a per-mode override is not yet wired through the CLI).
