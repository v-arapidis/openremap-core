"""
C166/ST10 reference collection for the code-reference (xref) signal.

Thin Python adapter over the Rust decoder (``openremap._rust.c166_references``,
see ``_rs/src/arch/c166.rs``) plus the address translation that turns raw
16-bit direct-memory operands into file offsets.  There is deliberately
NO Python decoder — the disassembly is Rust-only (notes/arch/plan.md §6).

C166 addressing: a 16-bit logical data address is windowed at runtime
through a DPP register — physical = (DPP[page] << 14) | (addr & 0x3FFF),
where page = addr >> 14 and DPP0–DPP3 are set once at boot.  The boot code
writes them with ``MOV DPPx, #pag`` (E6 <sfr-index> <lo> <hi>, sfr-index
0–3 = DPP0–DPP3; opcode/size table in ``_rs/src/arch/c166.rs``, register
attachment in the Ghidra C166 SLEIGH spec).  The preferred translation
reads those boot values (``find_dpp_init``) and resolves every reference
to its exact 24-bit physical address, then detects the per-file flash base
B (physical = file + B) by hit-testing a small candidate list against the
table data spans (``detect_dpp_base`` — the C166 analogue of the TriCore
``_detect_base``).  When the boot DPP init is absent or does not resolve
cleanly, the pass falls back to the empirical window search
(``detect_window``): file = (addr & 0x3FFF) + W for every 16 KB window W.

Measured on the real corpus (2026-08-27): the DPP path recovers ME7's
exact flash base 0x800000 (the window search only ever found the
coincidental 16 KB window 0x14000), while EDC15/MS43 — whose direct-memory
references largely use EXTP/EXTS page overrides that static analysis
cannot follow — fall back to the window search unchanged.
"""

from __future__ import annotations

from openremap._rust import (  # type: ignore[import-untyped]
    c166_references as _rust_c166_references,
    c166_walk as _rust_c166_walk,
)

#: Minimum translated references landing inside the data spans before a
#: DPP window base is trusted.  C166 offsets are 16-bit and dense, so this
#: is higher than the TriCore ``_MIN_BASE_HITS`` (3) — a wrong window on a
#: dense-table binary can score hundreds of accidental hits, and the bar
#: must sit above that noise.
_MIN_WINDOW_HITS = 8

#: DPP window size (16 KB) — the granularity of C166 windowed addressing.
_WINDOW = 0x4000

#: Candidate physical flash bases for DPP-value address translation
#: (physical = file + B).  The C166 analogue of ``refs._BASE_CANDIDATES``:
#: ME7/ME9 flash lives at 0x800000, other Bosch/Siemens C166 family bases
#: sit in the 0x100000–0x400000 band; 0 covers identity-mapped images.
_DPP_BASE_CANDIDATES = (0, 0x800000, 0x100000, 0x200000, 0x400000)

#: How much of the file start is scanned for the boot-time DPP0–3 init.
#: The reset-vector startup code (and the ISR prologues that immediately
#: follow it) live in the first 128 KB of every real C166 image seen.
_DPP_BOOT_LIMIT = 0x20000

#: SFR register index -> DPP number for the ``MOV DPPx, #pag`` second byte
#: (E6 <index> <lo> <hi>; DPP0–DPP3 are SFRs 0xFE00–0xFE06).
_DPP_SFR_INDEX = {0x00: 0, 0x01: 1, 0x02: 2, 0x03: 3}


def collect_references(
    data: bytes, regions: list[tuple[int, int]]
) -> tuple[list[tuple[int, int]], int]:
    """((offset16, insn_addr) pairs, instruction_count) for *regions*.

    The Rust decoder returns the raw DPP-windowed 16-bit operands of the
    direct-memory forms plus the number of decoded instructions.
    """
    refs, insn_count = _rust_c166_references(data, [(s, e) for s, e in regions])
    return [(int(off), int(addr)) for off, addr in refs], int(insn_count)


def walk(data: bytes, regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """(insn_offset, length) for every decoded instruction in *regions*.

    The raw decode stream (same walk that ``collect_references`` projects
    from) — used by the validation harnesses (walk-parity vs the nefmoto
    parser, the Ghidra diff) and future arch tooling.
    """
    return [
        (int(off), int(size))
        for off, size in _rust_c166_walk(data, [(s, e) for s, e in regions])
    ]


def detect_window(
    offsets: list[int], file_size: int, spans: list[tuple[int, int]]
) -> tuple[int, int]:
    """Best DPP window file-offset base W and its hit count.

    file = (offset & 0x3FFF) + W.  Picks the 16 KB window whose translated
    offsets hit the data spans most often; returns (0, hits) when no
    window clears ``_MIN_WINDOW_HITS`` (the pass then reports no
    references — presence-only: no signal is never a penalty).
    """
    best_w, best_hits = 0, 0
    for w in range(0, file_size, _WINDOW):
        hits = 0
        for o in set(offsets):
            f = (o & 0x3FFF) + w
            if f < file_size and any(s <= f < e for s, e in spans):
                hits += 1
        if hits > best_hits:
            best_w, best_hits = w, hits
    if best_hits < _MIN_WINDOW_HITS:
        return 0, best_hits
    return best_w, best_hits


def find_dpp_init(data: bytes, limit: int = _DPP_BOOT_LIMIT) -> tuple | None:
    """The boot-time DPP0–3 values, or None when not cleanly found.

    Boot code initialises the page registers with ``MOV DPPx, #pag`` =
    ``E6 <sfr-index> <lo> <hi>`` (little-endian 16-bit page).  The first
    boot-area write to each register wins (the reset-vector startup code
    and the ISR prologues right after it — both real init sites, verified
    against Ghidra on ME7/EDC15/MS43).  Returns ``(DPP0, DPP1, DPP2,
    DPP3)`` only when all four are found with sane 14-bit page values;
    otherwise None (the caller falls back to the window search —
    presence-only: no signal is never a penalty).

    The scan is a raw byte-pattern scan, deliberately not tied to the
    Rust walk's alignment: the boot area may begin with padding or a
    version string (e.g. EDC15 dumps start with 0xC3 fill), and the
    walk's linear decode can drift through such non-code bytes.
    """
    if not data:
        return None
    end = min(len(data), limit)
    found: dict[int, int] = {}
    i = 0
    while i + 4 <= end:
        i = data.find(b"\xe6", i)
        if i < 0 or i + 4 > end:
            break
        idx = _DPP_SFR_INDEX.get(data[i + 1])
        if idx is not None and idx not in found:
            val = int.from_bytes(data[i + 2 : i + 4], "little")
            if val < 0x4000:  # DPP registers hold a 14-bit page number
                found[idx] = val
        i += 1
    if len(found) == 4:
        return tuple(found[i] for i in range(4))
    return None


def detect_dpp_base(
    offsets: list[int], dpp: tuple, file_size: int, spans: list[tuple[int, int]]
) -> tuple[int, int] | None:
    """Flash base B and hit count for DPP-value resolution, or None.

    With the boot DPP values, each reference's physical address is exact:
    phys = (DPP[offset >> 14] << 14) | (offset & 0x3FFF), and the file is
    a linear image phys = file + B.  Hit-test the candidate flash bases
    (``_DPP_BASE_CANDIDATES``) against the data spans and require a clear
    winner: at least ``_MIN_WINDOW_HITS`` and twice the runner-up.  The
    margin is essential — on dense-table binaries a *wrong* base can score
    hundreds of accidental hits (the window search's single-window result
    is exactly such a coincidence), so the DPP path must only fire when
    one base stands out.
    """
    scores = []
    for b in _DPP_BASE_CANDIDATES:
        hits = 0
        for o in set(offsets):
            f = ((dpp[o >> 14] << 14) | (o & 0x3FFF)) - b
            if 0 <= f < file_size and any(s <= f < e for s, e in spans):
                hits += 1
        scores.append((hits, b))
    scores.sort(reverse=True)
    best_hits, best_base = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0
    if best_hits >= _MIN_WINDOW_HITS and best_hits >= 2 * runner_up:
        return best_base, best_hits
    return None
