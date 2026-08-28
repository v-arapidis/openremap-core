#!/usr/bin/env python
"""
scripts/verify_c166.py — C166 decoder parity harness (Ghidra oracle).

Disassembles real C166 binaries (ME7 / MS43 / EDC15 / EDC16 / PPD / SID)
with BOTH engines and reports per-instruction parity:

  - **Ghidra** via pyghidra + the mumbel/Ghidra_C166 SLEIGH extension
    (Apache-2.0 — dev-machine oracle only, never a runtime dependency)
  - **our Rust decoder** (`openremap._rust.c166_walk` / `c166_references`,
    `_rs/src/arch/c166.rs`)

Parity check (full walk): every (insn_offset, length) our decoder produces
must match a Ghidra-disassembled instruction at the same address with the
same length.  Reference-bearing forms additionally check the mnemonic is a
MOV/MOVB memory form.

Setup (dev machine, once):
    1. Ghidra + pyghidra + the C166 SLEIGH extension live in `ghidra/`
       at the repo root (see the ghidra/ folder; pyghidra venv at
       `ghidra/venv`).  The harness auto-discovers them, or point
       GHIDRA_INSTALL_DIR at the extracted Ghidra.
    2. Run with the pyghidra venv's python:
         ghidra/venv/bin/python scripts/verify_c166.py <bin>...
    3. Ghidra writes its user config under the repo's `ghidra/user-config`
       (XDG_CONFIG_HOME is redirected if the default is unwritable).

Skips cleanly when pyghidra/Ghidra is absent — it is NOT run in CI.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from openremap.core.arch import c166
from openremap.core.services.maps.layout import code_regions_from_layout, segment
from openremap.core.services.maps.map_hunter import scan_map_tables

#: SLEIGH language id from the mumbel/Ghidra_C166 extension (c166.ldefs).
_C166_LANGUAGE = "C166:LE:16:default"

#: Reference-bearing forms the xref signal collects (mnemonics as Ghidra
#: renders them — lowercase, from the SLEIGH spec's constructor names).
_DIRECT_MEM_MNEMONICS = ("mov", "movb")


def _find_ghidra() -> Path | None:
    """The extracted Ghidra home, or None."""
    env = os.environ.get("GHIDRA_INSTALL_DIR")
    if env:
        p = Path(env)
        if (p / "Ghidra").is_dir():
            return p
    repo = Path(__file__).resolve().parents[1]
    hits = sorted((repo / "ghidra").glob("ghidra_*_PUBLIC"))
    for h in hits:
        if (h / "Ghidra").is_dir():
            return h
    return None


def _ensure_user_config() -> None:
    """Ghidra needs a writable user-config dir; redirect if needed."""
    if not os.access(os.path.expanduser("~/.config/ghidra"), os.W_OK):
        repo = Path(__file__).resolve().parents[1]
        os.makedirs(repo / "ghidra" / "user-config", exist_ok=True)
        os.environ["XDG_CONFIG_HOME"] = str(repo / "ghidra" / "user-config")


def _ghidra_disasm(
    data: bytes, regions: list[tuple[int, int]], walk: list[tuple[int, int]]
) -> dict[int, tuple[int, str]]:
    """Decode at every walk boundary with Ghidra; return {addr: (len, mnemonic)}.

    The raw-bin loader maps the C166 file 1:1 (one memory block at
    address 0 — file offset == address).  Flow-following disassembly dies
    at the boot vectors, so we drive Ghidra's decoder at **our** walk
    boundaries — one `disassemble(addr)` per instruction — which is the
    per-instruction oracle the parity check needs.  A desynced walk
    boundary either fails to decode (missing) or decodes a different
    length (mismatch).
    """
    import pyghidra  # dev-only, absent on most machines

    _ensure_user_config()
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(data)
        bin_path = f.name
    try:
        pyghidra.start()
        out: dict[int, tuple[int, str]] = {}
        containing: dict[int, int] = {}
        with pyghidra.open_program(bin_path, language=_C166_LANGUAGE, analyze=False) as flat_api:
            listing = flat_api.getCurrentProgram().getListing()
            region_set = {(s, e) for s, e in regions}
            for addr, _size in walk:
                if not any(s <= addr < e for s, e in region_set):
                    continue
                try:
                    flat_api.disassemble(flat_api.toAddr(addr))
                except Exception:
                    continue  # oracle decode failure at this boundary
                insn = listing.getInstructionAt(flat_api.toAddr(addr))
                if insn is not None:
                    out[addr] = (int(insn.getLength()), insn.getMnemonicString())
            # characterize the missing: our boundary inside another Ghidra
            # instruction (call-sequencing artifact) vs nothing decoded at all
            for addr, _size in walk:
                if addr not in out:
                    try:
                        insn = listing.getInstructionContaining(flat_api.toAddr(addr))
                        if insn is not None and int(insn.getMinAddress().getOffset()) != addr:
                            containing[addr] = int(insn.getMinAddress().getOffset())
                    except Exception:
                        pass
        return out, containing
    finally:
        os.unlink(bin_path)


def verify(path: str) -> int:
    data = Path(path).read_bytes()
    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    codes = code_regions_from_layout(regions)

    walk = c166.walk(data, codes)  # (insn_addr, length)
    refs, _ = c166.collect_references(data, codes)  # (operand, insn_addr)
    ref_addrs = {addr for _, addr in refs}
    print(f"{Path(path).name}: {len(walk)} insns, {len(refs)} refs over {len(codes)} regions")

    try:
        ghidra, ghidra_containing = _ghidra_disasm(data, codes, walk)
    except ImportError:
        print("  skipped — pyghidra/Ghidra not installed (dev-machine oracle only)")
        return 0

    if not ghidra:
        print("  FAILED — Ghidra produced no instructions (check the C166 extension + language id)")
        return 1

    len_mismatch = 0
    missing = 0
    missing_inside = 0  # our boundary lands inside a Ghidra instruction (call-sequencing artifact)
    missing_opcodes: Counter = Counter()  # first bytes Ghidra cannot decode at all
    mnemonic_bad = 0
    matched = 0

    diff_opcodes: Counter = Counter()  # (our_opcode, our_size, ghidra_len) for length diffs
    diff_sites: list[int] = []
    for addr, size in walk:
        g = ghidra.get(addr)
        if g is None:
            missing += 1
            containing = ghidra_containing.get(addr)
            if containing is not None:
                missing_inside += 1
            else:
                missing_opcodes[data[addr]] += 1
        elif g[0] != size:
            len_mismatch += 1
            diff_opcodes[(data[addr], size, g[0])] += 1
            if len(diff_sites) < 20:
                diff_sites.append(addr)
        else:
            matched += 1
            if addr in ref_addrs and g[1].lower() not in _DIRECT_MEM_MNEMONICS:
                mnemonic_bad += 1

    total = len(walk)
    print(
        f"  parity: {matched}/{total} lengths match Ghidra "
        f"({100.0 * matched / total:.3f}%) — missing {missing} "
        f"(inside-other-insn {missing_inside}, undecodable-opcode {sum(missing_opcodes.values())}), "
        f"length-diff {len_mismatch}, ref-mnemonic-diff {mnemonic_bad}"
    )
    if missing_opcodes:
        top = missing_opcodes.most_common(8)
        print("  top undecodable first-bytes (Ghidra spec gaps): "
              + ", ".join(f"0x{o:02X}x{c}" for o, c in top))
    if diff_sites:
        walk_map = dict(walk)
        for a in diff_sites:
            print(f"    LEN DIFF @0x{a:X}: opcode 0x{data[a]:02X} ours={walk_map[a]} — bytes: "
                  + " ".join(f"{data[a + i]:02X}" for i in range(8)))
    if diff_opcodes:
        print("  top length-diff opcodes (our_opcode, our_size, ghidra_len): "
              + ", ".join(f"0x{o:02X}/{s}/{gl}x{c}" for (o, s, gl), c in diff_opcodes.most_common(10)))
    bad = missing + len_mismatch
    return 1 if (bad > 0 or mnemonic_bad > 0) else 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ghidra = _find_ghidra()
    if ghidra:
        os.environ.setdefault("GHIDRA_INSTALL_DIR", str(ghidra))
        print(f"Ghidra: {ghidra}")
    total = 0
    for path in sys.argv[1:]:
        total += verify(path)
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
