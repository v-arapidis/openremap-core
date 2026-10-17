# Contributing: CPU / architecture decoders

> Paths in this guide are relative to the repository root.

Decoders are what let OpenRemap read *code* instead of just tables: they power
the code-reference signal, CPU auto-detection, and the `routine` command's
pseudo-decompiler. They are also the domain with the strictest evidence bar,
because a decoder that is subtly wrong produces confident nonsense.

Read [../CONTRIBUTING.md](../CONTRIBUTING.md) first.

## Where things live

| Piece | Location | Role |
|---|---|---|
| CPU adapters | `openremap/core/arch/<cpu>.py` | Thin Python layer over a Rust decoder or capstone |
| Rust decoders | `openremap/_rs/src/arch/<cpu>.rs` | Written from scratch for CPUs capstone cannot do (C166/ST10, 8051, MCS-96) |
| Capstone-backed | `openremap/core/arch/{motorola,ppc,sh,tricore,x86}.py` | Mature third-party disassembly |
| Detection | `openremap/core/arch/detect.py` | `detect_arch()` — candidate table + per-arch gating |
| Cross-references | `openremap/core/arch/refs.py` | `collect_xrefs()` with per-arch collectors |
| Pseudo-code | `openremap/core/arch/routine.py`, `pseudocode.py` | The `routine` command's rendering |

`openremap/core/arch/c166.py` is the reference shape for a Rust-backed adapter:
it imports `c166_disasm`, `c166_references` and `c166_walk` from
`openremap._rust` and keeps only orchestration in Python.

## Adding a decoder

1. **Decide the implementation.** If capstone already covers the CPU, add an
   adapter next to `motorola.py`/`ppc.py`. If it does not, the decoder goes in
   Rust (`_rs/src/arch/`) and Python stays a thin adapter. Hot paths belong in
   Rust — see [map-scoring.md](map-scoring.md) for the rebuild route.
2. **Wire detection** in `detect.py`: the candidate table, plus any gate that
   keeps the decoder from claiming foreign code. `detect_arch()` already
   demonstrates the pattern — the C166 candidate is rejected unless a boot DPP
   init (`c166.find_dpp_init`) is present, because presence-only signals
   otherwise false-positive on unrelated binaries.
3. **Add a collector** in `refs.py` if the architecture produces usable
   cross-references (`_collect_c166`, `_collect_mcs51`, `_collect_mcs96` are the
   existing ones).
4. **Verify against an independent oracle.** This is not optional: every
   in-tree Rust decoder was checked against something outside this repository
   (Ghidra's SLEIGH spec for C166, the `at51` disassembler for 8051 — 100 %
   agreement over ~312k real instruction boundaries — and MAME's opcode tables
   plus Ghidra for MCS-96). State your oracle and the agreement rate in the PR.
5. **Keep the oracle out of the test suite.** Oracle tools are dev-machine
   only; tests must skip cleanly when they are absent, so CI stays green on a
   bare checkout.

## What a PR must show

- The CPU, the families that use it, and the instruction forms you cover —
  including what you deliberately do **not** decode.
- The oracle you compared against and the measured agreement (boundaries
  matched / total, on real code regions, not on hand-picked instructions).
- A parity test Python-vs-Rust if you added a Rust path, and a synthetic test
  that does not require any binary file.
- For detection changes: evidence that binaries previously claimed by another
  arch still land where they did.

## Why the bar is high

Cross-reference resolution, base-address guessing and code/data separation all
depend on the decoder being right about instruction *boundaries*. A single
byte of drift moves every subsequent instruction, and the failure is silent: the
output still looks like plausible assembly. When in doubt, decode less and say
so.
