//! C166/ST10 reference extraction — the xref signal's decoder.
//!
//! A decode-the-reference-bearing-forms-only C166 decoder (NOT the full
//! ISA — the xref signal needs only direct-memory operands, see
//! notes/arch/plan.md §6).  Walks code regions with the nefmoto-derived
//! size table (seeded from `core/services/checksums/nefmoto.py`, verified
//! against the public ST10/C166 instruction-set manuals) and collects the
//! 16-bit direct-memory addresses of:
//!
//! ```text
//!   MOV  [Rwn], mem   0x84     MOVB [Rwn], mem   0xA4
//!   MOV  mem, [Rwn]   0x94     MOVB mem, [Rwn]   0xB4
//!   MOV  Rwn, mem     0xF2     MOVB Rbn, mem     0xF3
//!   MOV  mem, Rwn     0xF6     MOVB mem, Rbn     0xF7
//! ```
//!
//! These offsets are DPP-windowed at runtime (physical = page << 14 |
//! (addr & 0x3FFF), page set once at boot); the Python arch layer
//! (`core/arch/c166.py`) hit-tests them against table data spans to find
//! the window base — the same empirical approach as the TriCore load-base
//! detection.  Instruction sizing is intentionally minimal: known 4-byte
//! forms are sized exactly, everything else defaults to 2 bytes (the
//! C166's dominant form), which keeps the walk aligned in practice
//! (measured on the real ME7/EDC15/MS43 corpus).
//!
//! Ground truth: port of the nefmoto parser's size knowledge + the public
//! C166 ISA (THIRD_PARTY.md); no Ghidra SLEIGH code is copied.

use pyo3::prelude::*;

/// Instruction length in bytes (2 or 4) for the first byte of a C166
/// instruction.
///
/// The 4-byte set is generated from the mumbel/Ghidra_C166 SLEIGH spec
/// (`data/languages/c166.slaspec` — every definition whose operands consume
/// a second 16-bit word, i.e. a `; operand` after the `is` clause, in both
/// the `op0007` and the `op0003`+constructor encodings; zero
/// opcode-length conflicts exist, so the first byte fully determines the
/// size) plus the forms the spec's `...`-abbreviated or missing
/// definitions cover:
///   - 0xDA CALLS / 0xFA JMPS (spec defs use `...`; 4 bytes per nefmoto)
///   - 0xF5 MOVB Rbn,[Rwm+#data16] (spec gap; manual)
/// Everything else defaults to 2 bytes — the C166's dominant form.
/// Oracle-verified against Ghidra on the real corpus: ~99.9% instruction-
/// length agreement (see scripts/verify_c166.py).
fn insn_size(b0: u8) -> usize {
    // JMPR: every 0x?D opcode is a 2-byte conditional jump.
    if b0 & 0x0F == 0x0D {
        return 2;
    }
    match b0 {
        0x02 | 0x03 | 0x04 | 0x05 | 0x06 | 0x07 | 0x0A | 0x12 => 4,
        0x13 | 0x14 | 0x15 | 0x16 | 0x17 | 0x1A | 0x22 | 0x23 => 4,
        0x24 | 0x25 | 0x26 | 0x27 | 0x2A | 0x32 | 0x33 | 0x34 => 4,
        0x35 | 0x36 | 0x37 | 0x3A | 0x42 | 0x43 | 0x46 | 0x47 => 4,
        0x4A | 0x52 | 0x53 | 0x54 | 0x55 | 0x56 | 0x57 | 0x5A => 4,
        0x62 | 0x63 | 0x64 | 0x65 | 0x66 | 0x67 | 0x6A | 0x72 => 4,
        0x73 | 0x74 | 0x75 | 0x76 | 0x77 | 0x7A | 0x82 | 0x84 => 4,
        0x85 | 0x86 | 0x87 | 0x8A | 0x92 | 0x94 | 0x96 | 0x97 => 4,
        0x9A | 0xA2 | 0xA4 | 0xA5 | 0xA6 | 0xA7 | 0xAA | 0xB2 => 4,
        0xB4 | 0xB5 | 0xB6 | 0xB7 | 0xBA | 0xC2 | 0xC4 | 0xC5 => 4,
        0xC6 | 0xCA | 0xD2 | 0xD4 | 0xD5 | 0xD6 | 0xD7 | 0xDA => 4,
        0xE2 | 0xE4 | 0xE6 | 0xE7 | 0xEA | 0xF2 | 0xF3 | 0xF4 => 4,
        0xF5 | 0xF6 | 0xF7 | 0xFA => 4,
        _ => 2,
    }
}

/// True when the opcode is a direct-memory form whose 16-bit operand is a
/// DPP-windowed data address (the xref signal's reference-bearing forms).
fn is_direct_mem(b0: u8) -> bool {
    matches!(b0, 0x84 | 0x94 | 0xA4 | 0xB4 | 0xF2 | 0xF3 | 0xF6 | 0xF7)
}

/// Collect the direct-memory reference operands of the C166 code regions.
///
/// Returns `((offset16, insn_file_offset) pairs, instruction_count)`: the
/// raw 16-bit DPP-windowed addresses and the file offset of the
/// referencing instruction, plus the number of decoded instructions
/// (walk steps — approximate, unknown opcodes default to 2 bytes).  The
/// caller hit-tests the offsets against table spans to recover the DPP
/// window base (see `core/arch/c166.py`).
#[pyfunction]
pub fn c166_references(
    data: &[u8],
    regions: Vec<(i64, i64)>,
) -> (Vec<(u32, u32)>, u32) {
    let mut out: Vec<(u32, u32)> = Vec::new();
    let mut insn_count: u32 = 0;
    for_each_insn(data, &regions, |off, _size| {
        insn_count += 1;
        let b0 = data[off];
        if is_direct_mem(b0) {
            let operand = u16::from_le_bytes([data[off + 2], data[off + 3]]) as u32;
            out.push((operand, off as u32));
        }
    });
    (out, insn_count)
}

/// Decode the instruction stream of the C166 code regions.
///
/// Returns `(insn_file_offset, length)` for every decoded instruction —
/// the raw walk, used by the validation harness (walk-parity vs the
/// corpus-validated nefmoto parser, and the Ghidra diff) and future arch
/// tooling.  `c166_references` is a projection of this walk.
#[pyfunction]
pub fn c166_walk(data: &[u8], regions: Vec<(i64, i64)>) -> Vec<(u32, u8)> {
    let mut out: Vec<(u32, u8)> = Vec::new();
    for_each_insn(data, &regions, |off, size| {
        out.push((off as u32, size as u8));
    });
    out
}

/// Mnemonic for the first byte of a C166 instruction — the common forms
/// (the checksum/map-access 80% use case).  Unknown opcodes render as
/// ``DB`` (data byte).  This is the phrasebook, not the full ISA: the
/// register/immediate arithmetic and move forms, the direct-memory forms,
/// and the control-transfer forms.
fn mnemonic(b0: u8) -> &'static str {
    if b0 & 0x0F == 0x0D {
        return "JMPR";
    }
    match b0 {
        0x00 | 0x02 | 0x04 | 0x06 | 0x08 => "ADD",
        0x10 | 0x12 | 0x14 | 0x16 | 0x18 => "ADDC",
        0x20 | 0x22 | 0x24 | 0x26 | 0x28 => "SUB",
        0x40 | 0x42 | 0x46 | 0x48 => "CMP",
        0x41 | 0x43 | 0x47 | 0x49 => "CMPB",
        0x84 | 0x88 | 0x94 | 0x98 | 0xA8 | 0xB8 | 0xC4 | 0xC8 | 0xD4 | 0xD8 | 0xE0 | 0xE6 | 0xE8 | 0xF0 | 0xF2 | 0xF6 => "MOV",
        0x89 | 0x99 | 0xA4 | 0xA9 | 0xB4 | 0xB9 | 0xC9 | 0xD9 | 0xE1 | 0xE4 | 0xE7 | 0xE9 | 0xF1 | 0xF3 | 0xF4 | 0xF7 => "MOVB",
        0xDA => "CALLS",
        0xFA => "JMPS",
        0xEA => "JMPA",
        0x9C => "JMPI",
        0xCC => "NOP",
        0xCB => "RET",
        0xDB => "RETS",
        0xFB => "RETI",
        0xD7 | 0xDC => "EXTP",
        _ => "DB",
    }
}

/// EXTP/EXTS/EXTPR/EXTSR — the 0xD7/0xDC opcode's actual name is selected
/// by the two high bits of the second byte (``op1415``).
fn exts_name(b0: u8, b1: u8) -> &'static str {
    match (b1 >> 6) & 0x3 {
        0 => "EXTS",
        2 => "EXTSR",
        3 => "EXTPR",
        _ => "EXTP",
    }
}

/// Word-register operand from the low nibble of the second byte.
fn rw(b1: u8) -> String {
    format!("R{}", b1 & 0x0F)
}

/// Word-register operand from the high nibble of the second byte.
fn rw_hi(b1: u8) -> String {
    format!("R{}", b1 >> 4)
}

/// Byte-register operand from the low nibble of the second byte.
fn rb(b1: u8) -> String {
    format!("RB{}", b1 & 0x0F)
}

/// Byte-register operand from the high nibble of the second byte.
fn rb_hi(b1: u8) -> String {
    format!("RB{}", b1 >> 4)
}

/// Register/SFR operand (``reg0815``) — a word register when the high
/// nibble of the second byte is ``0xF`` (then the register is the low
/// nibble), else a special-function register addressed by the whole byte
/// (SLEIGH ``reg0815_w``: ``r0811 & op1215=0xF`` vs the sfr/esfr arms).
fn reg8(b1: u8) -> String {
    if b1 >> 4 == 0xF {
        format!("R{}", b1 & 0x0F)
    } else {
        format!("SFR 0x{:02X}", b1)
    }
}

/// Byte-register/SFR operand (``reg0815_b``) — same register-vs-SFR split
/// as :func:`reg8`, for the byte forms.
fn rb8(b1: u8) -> String {
    if b1 >> 4 == 0xF {
        format!("RB{}", b1 & 0x0F)
    } else {
        format!("SFR 0x{:02X}", b1)
    }
}

/// 16-bit immediate/memory operand at bytes ``off+2..off+3`` (little-endian).
fn imm16(data: &[u8], off: usize) -> String {
    format!("0x{:04X}", u16::from_le_bytes([data[off + 2], data[off + 3]]))
}

/// The shared ``op2`` second operand of the 2-byte arithmetic forms
/// (``CMP/CMPB/SUB/ADD/ADDC 0x?8``): a register when bit 3 of the second
/// byte is set, else a 3-bit immediate (nefmoto's `_data3_subswitch`).
fn op2(b1: u8) -> String {
    if b1 & 0x08 != 0 {
        format!("R{}", b1 & 0x0F)
    } else {
        format!("#0x{:X}", b1 & 0x07)
    }
}

/// Decode the operands of a C166 instruction into a readable string.
///
/// Operand encodings are read from the Ghidra_C166 SLEIGH spec
/// (`c166.slaspec`): ``op0007`` = first byte, ``op0811``/``op1215`` = the
/// low/high nibble of the second byte, ``mem/data1631`` = the 16-bit
/// little-endian word at bytes 2–3.  Not the full ISA — register + direct
/// memory + immediate forms (the phrasebook use case).
fn operands(data: &[u8], off: usize, b0: u8, size: usize) -> String {
    if b0 & 0x0F == 0x0D {
        // JMPR cc, rel — render the absolute target (rel is signed).
        let rel = data[off + 1] as i8 as i32;
        let target = off as i32 + 2 + rel;
        return format!("0x{:04X}", target.max(0) as u32);
    }
    let b1 = data[off + 1];
    match b0 {
        // MOV word forms
        0xE0 => format!("{}, #0x{:X}", rw(b1), b1 >> 4),
        0xF0 => format!("{}, {}", rw_hi(b1), rw(b1)),
        0xD4 => format!("{}, [{} + #{}]", rw_hi(b1), rw(b1), imm16(data, off)),
        0x98 => format!("{}, [{}+]", rw_hi(b1), rw(b1)),
        0xA8 => format!("{}, [{}]", rw_hi(b1), rw(b1)),
        0x88 => format!("[-{}], {}", rw(b1), rw_hi(b1)),
        0xC4 => format!("[{} + #{}], {}", rw(b1), imm16(data, off), rw_hi(b1)),
        0xB8 => format!("[{}], {}", rw(b1), rw_hi(b1)),
        0xD8 => format!("[{}+], [{}]", rw(b1), rw_hi(b1)),
        0xE8 => format!("[{}], [{}+]", rw(b1), rw_hi(b1)),
        0xC8 => format!("[{}], [{}]", rw(b1), rw_hi(b1)),
        0x84 => format!("[{}], {}", rw(b1), imm16(data, off)),
        0x94 => format!("{}, [{}]", imm16(data, off), rw(b1)),
        0xF6 => format!("{}, {}", imm16(data, off), reg8(b1)),
        0xE6 => format!("{}, #{}", reg8(b1), imm16(data, off)),
        0xF2 => format!("{}, {}", reg8(b1), imm16(data, off)),
        // MOVB byte forms
        0xE1 => format!("{}, #0x{:X}", rb(b1), b1 >> 4),
        0xF1 => format!("{}, {}", rb_hi(b1), rb(b1)),
        0xF4 => format!("{}, [{} + #{}]", rb_hi(b1), rw(b1), imm16(data, off)),
        0x99 => format!("{}, [{}+]", rb_hi(b1), rw(b1)),
        0xA9 => format!("{}, [{}]", rb_hi(b1), rw(b1)),
        0x89 => format!("[-{}], {}", rw(b1), rb_hi(b1)),
        0xE4 => format!("[{} + #{}], {}", rw(b1), imm16(data, off), rb_hi(b1)),
        0xB9 => format!("[{}], {}", rw(b1), rb_hi(b1)),
        0xD9 => format!("[{}+], [{}]", rw_hi(b1), rw(b1)),
        0xE9 => format!("[{}], [{}+]", rw_hi(b1), rw(b1)),
        0xC9 => format!("[{}], [{}]", rw_hi(b1), rw(b1)),
        0xA4 => format!("[{}], {}", rw(b1), imm16(data, off)),
        0xB4 => format!("{}, [{}]", imm16(data, off), rw(b1)),
        0xF7 => format!("{}, {}", imm16(data, off), rb8(b1)),
        0xE7 => format!("{}, #{}", rb8(b1), imm16(data, off)),
        0xF3 => format!("{}, {}", rb8(b1), imm16(data, off)),
        // arithmetic 2-byte forms: Rwn, op2
        0x00 | 0x10 | 0x20 | 0x40 => format!("{}, {}", rw_hi(b1), op2(b1)),
        0x41 => format!("{}, {}", rb_hi(b1), op2(b1)),
        0x08 | 0x18 | 0x28 | 0x48 => format!("{}, {}", rw_hi(b1), op2(b1)),
        0x49 => format!("{}, {}", rb_hi(b1), op2(b1)),
        // arithmetic 4-byte forms
        0x04 | 0x14 | 0x24 => format!("{}, {}", imm16(data, off), reg8(b1)),
        0x02 | 0x12 | 0x22 | 0x42 => format!("{}, {}", reg8(b1), imm16(data, off)),
        0x06 | 0x16 | 0x26 | 0x46 => format!("{}, #{}", reg8(b1), imm16(data, off)),
        0x43 => format!("{}, {}", rb8(b1), imm16(data, off)),
        0x47 => format!("{}, #{}", rb8(b1), imm16(data, off)),
        // control transfer
        0xDA | 0xFA => format!("0x{:02X}:0x{:04X}", b1, u16::from_le_bytes([data[off + 2], data[off + 3]])),
        0xEA => format!("0x{:04X}", u16::from_le_bytes([data[off + 2], data[off + 3]])),
        0x9C => format!("[{}]", rw(b1)),
        0xCC | 0xCB | 0xDB | 0xFB => String::new(),
        0xD7 => format!("#0x{:02X}, #0x{:X}", b1, (b1 >> 4) & 0x3),
        0xDC => format!("{}, #0x{:X}", rw(b1), (b1 >> 4) & 0x3),
        // data byte / unknown
        _ => format!("0x{:02X}", b0),
    }
}

/// Disassemble the instruction stream of the C166 code regions.
///
/// Returns ``(insn_file_offset, length, mnemonic, operands)`` for every
/// decoded instruction — the phrasebook rendering the pseudo-decompiler
/// consumes.  Operand strings are readable but not full-ISA (register +
/// direct-memory + immediate forms); unknown opcodes render as ``DB 0xXX``.
#[pyfunction]
pub fn c166_disasm(data: &[u8], regions: Vec<(i64, i64)>) -> Vec<(u32, u8, String, String)> {
    let mut out: Vec<(u32, u8, String, String)> = Vec::new();
    for_each_insn(data, &regions, |off, size| {
        let b0 = data[off];
        let b1 = data[off + 1];
        let mnem = if b0 == 0xD7 || b0 == 0xDC { exts_name(b0, b1) } else { mnemonic(b0) };
        out.push((
            off as u32,
            size as u8,
            mnem.to_string(),
            operands(data, off, b0, size),
        ));
    });
    out
}

/// Run *f* for every decoded instruction in the code regions.
///
/// Shared walk used by `c166_references` and `c166_walk`: same size table,
/// same region bounds, so the two exports can never drift apart.  Only
/// instructions that **fully fit inside the region** are emitted — a
/// truncated tail instruction is not a valid instruction of this region.
fn for_each_insn<F>(data: &[u8], regions: &[(i64, i64)], mut f: F)
where
    F: FnMut(usize, usize),
{
    for &(s, e) in regions {
        if e <= s {
            continue;
        }
        let mut off = s.max(0) as usize;
        let end = (e as usize).min(data.len());
        while off + 1 < end {
            let b0 = data[off];
            let size = insn_size(b0);
            if off + size <= end {
                f(off, size);
                off += size;
            } else {
                break; // region ends mid-instruction
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn refs(data: &[u8], start: usize, end: usize) -> Vec<(u32, u32)> {
        c166_references(data, vec![(start as i64, end as i64)]).0
    }

    #[test]
    fn sizes_of_known_forms() {
        assert_eq!(insn_size(0xE6), 4); // MOV reg, #data16
        assert_eq!(insn_size(0xE0), 2); // MOV reg, #data4
        assert_eq!(insn_size(0xDA), 4); // CALLS
        assert_eq!(insn_size(0xEA), 4); // JMPA
        assert_eq!(insn_size(0xDC), 2); // EXTP Rwn
        assert_eq!(insn_size(0xD7), 4); // EXTP #seg
        assert_eq!(insn_size(0x84), 4); // MOV [Rwn], mem
        assert_eq!(insn_size(0xF2), 4); // MOV Rwn, mem
        assert_eq!(insn_size(0x90), 2); // unknown -> default 2
        assert_eq!(insn_size(0x0D), 2); // JMPR (low nibble 0xD)
        assert_eq!(insn_size(0x1D), 2); // JMPR family
    }

    #[test]
    fn direct_mem_forms_collect_operand() {
        // MOV R0, 0x1234 -> F2 00 34 12 (LE u16 at off+2)
        let d = [0xF2, 0x00, 0x34, 0x12];
        assert_eq!(refs(&d, 0, 4), vec![(0x1234, 0)]);

        // MOVB mem, Rbn -> F7 08 78 56
        let d = [0xF7, 0x08, 0x78, 0x56];
        assert_eq!(refs(&d, 0, 4), vec![(0x5678, 0)]);

        // MOV [R2], mem (0x84) then MOV mem, [R3] (0x94)
        let d = [0x84, 0x20, 0x90, 0x00, 0x94, 0x30, 0x00, 0x80];
        assert_eq!(refs(&d, 0, 8), vec![(0x0090, 0), (0x8000, 4)]);

        // MOVB [R1], mem (0xA4) then MOVB mem, [R1] (0xB4)
        let d = [0xA4, 0x10, 0x12, 0x34, 0xB4, 0x10, 0x43, 0x21];
        assert_eq!(refs(&d, 0, 8), vec![(0x3412, 0), (0x2143, 4)]);
    }

    #[test]
    fn non_memory_forms_are_not_refs() {
        // MOV reg, #data16 (0xE6) is an immediate load — NOT a reference.
        let d = [0xE6, 0x04, 0x72, 0xA8, 0xE6, 0x05, 0x87, 0x00];
        assert_eq!(refs(&d, 0, 8), Vec::<(u32, u32)>::new());

        // Indexed [Rw+#data16] forms (0xC4/0xD4/0xE4/0xF4) are
        // register-relative — NOT statically resolvable -> not refs.
        let d = [
            0xC4, 0x00, 0x34, 0x12, 0xD4, 0x00, 0x34, 0x12, //
            0xE4, 0x00, 0x34, 0x12, 0xF4, 0x00, 0x34, 0x12,
        ];
        assert_eq!(refs(&d, 0, 16), Vec::<(u32, u32)>::new());
    }

    #[test]
    fn walk_resizes_after_4byte_forms() {
        // MOV R0,mem (4B) + MOV R2,#data4 (2B) + MOV R1,mem (4B):
        // the second ref must be at offset 6, not 4.
        let d = [0xF2, 0x00, 0x00, 0x10, 0xE0, 0x20, 0xF2, 0x01, 0x00, 0x20];
        assert_eq!(refs(&d, 0, 10), vec![(0x1000, 0), (0x2000, 6)]);
    }

    #[test]
    fn region_bounds_and_mid_instruction_stop() {
        // Direct-mem form truncated by the region end -> no ref.
        let d = [0xF2, 0x00, 0x34, 0x12];
        assert_eq!(refs(&d, 0, 3), Vec::<(u32, u32)>::new());
        // Empty / inverted regions are skipped.
        assert_eq!(c166_references(&d, vec![(4, 2), (0, 0)]).0, Vec::<(u32, u32)>::new());
    }

    #[test]
    fn counts_walk_steps() {
        // 4 instructions in the region (2 known 4-byte + 2 default 2-byte).
        // NB: 0x92 is a 4-byte form per the Ghidra spec (CMP mem, dec-2) —
        // the trailing bytes are 0x93/0x93 so the count stays deterministic.
        let d = [0xF2, 0x00, 0x00, 0x10, 0x90, 0x91, 0xF6, 0x01, 0x00, 0x20, 0x93, 0x93];
        let (refs, count) = c166_references(&d, vec![(0, 12)]);
        assert_eq!(refs, vec![(0x1000, 0), (0x2000, 6)]);
        assert_eq!(count, 4);
    }

    #[test]
    fn sizes_verified_against_ghidra_spec() {
        // Opcodes the Ghidra SLEIGH spec defines as 4-byte that nefmoto's
        // table never covered (ALU/bit-op/CMP-inc-dec families) — the
        // oracle catch (scripts/verify_c166.py).
        for op in [0x64, 0x66, 0x65, 0x75, 0x86, 0xB2, 0x82, 0x92, 0x9A, 0xC2, 0x8A, 0xAA, 0xCA, 0xE2] {
            assert_eq!(insn_size(op), 4, "opcode 0x{op:02X} should be 4 bytes per the spec");
        }
        // 2-byte forms the spec keeps short (reg-reg / short-immediate).
        for op in [0xF0, 0xF1, 0x88, 0xA8, 0xB8, 0xC8, 0xE0, 0xE1, 0xF8, 0x90] {
            assert_eq!(insn_size(op), 2, "opcode 0x{op:02X} should be 2 bytes");
        }
    }

    #[test]
    fn walk_reports_offsets_and_sizes() {
        // MOV R0,mem (4B) + unknown (2B) + MOVB mem,Rbn (4B)
        let d = [0xF2, 0x00, 0x00, 0x10, 0x90, 0x91, 0xF7, 0x08, 0x78, 0x56];
        assert_eq!(
            c166_walk(&d, vec![(0, 10)]),
            vec![(0, 4), (4, 2), (6, 4)]
        );
        // region clipping: truncated tail instruction is dropped
        assert_eq!(c166_walk(&d, vec![(0, 9)]), vec![(0, 4), (4, 2)]);
    }

    #[test]
    fn disasm_renders_moves_and_arithmetic() {
        // MOV R0, 0x1234  (F2 F0 34 12: reg0815 high-nibble=0xF) + ADD R5, #6 (06 F5 06 00)
        let d = [0xF2, 0xF0, 0x34, 0x12, 0x06, 0xF5, 0x06, 0x00];
        let out = c166_disasm(&d, vec![(0, 8)]);
        assert_eq!(
            out,
            vec![
                (0, 4, "MOV".to_string(), "R0, 0x1234".to_string()),
                (4, 4, "ADD".to_string(), "R5, #0x0006".to_string()),
            ]
        );
    }

    #[test]
    fn disasm_renders_register_and_indirect_forms() {
        // MOV R3, R1 (F0 31: Rn=hi nibble, Rm=lo nibble) + MOV R7, [R2] (A8 72)
        // + MOV [R4], 0x00B3 (84 04 B3 00: Rn=lo nibble)
        let d = [0xF0, 0x31, 0xA8, 0x72, 0x84, 0x04, 0xB3, 0x00];
        let out = c166_disasm(&d, vec![(0, 8)]);
        assert_eq!(
            out,
            vec![
                (0, 2, "MOV".to_string(), "R3, R1".to_string()),
                (2, 2, "MOV".to_string(), "R7, [R2]".to_string()),
                (4, 4, "MOV".to_string(), "[R4], 0x00B3".to_string()),
            ]
        );
    }

    #[test]
    fn disasm_renders_byte_forms_and_branches() {
        // MOVB RB3, 0x12 (E7 F3 12 00) + JMPR cc, -2 (2D FE)
        let d = [0xE7, 0xF3, 0x12, 0x00, 0x2D, 0xFE];
        let out = c166_disasm(&d, vec![(0, 6)]);
        assert_eq!(
            out,
            vec![
                (0, 4, "MOVB".to_string(), "RB3, #0x0012".to_string()),
                (4, 2, "JMPR".to_string(), "0x0004".to_string()),
            ]
        );
    }

    #[test]
    fn disasm_sfr_operands() {
        // MOV DPP0, #0x0004 (E6 00 04 00) — reg0815 with high-nibble != 0xF
        // is an SFR, not a register.
        let d = [0xE6, 0x00, 0x04, 0x00];
        let out = c166_disasm(&d, vec![(0, 4)]);
        assert_eq!(out, vec![(0, 4, "MOV".to_string(), "SFR 0x00, #0x0004".to_string())]);
    }

    #[test]
    fn disasm_unknown_opcode_is_data_byte() {
        // 0x90 is not in the phrasebook table -> DB 0x90
        let d = [0x90, 0x91];
        let out = c166_disasm(&d, vec![(0, 2)]);
        assert_eq!(out, vec![(0, 2, "DB".to_string(), "0x90".to_string())]);
    }

    #[test]
    fn disasm_extp_exts_names() {
        // EXTP #page (D7 with op1415=1) vs EXTS (D7 with op1415=0)
        // op1415 = high 2 bits of byte 1; 0x40 -> 1, 0x00 -> 0
        let d = [0xD7, 0x40, 0x00, 0x00, 0xD7, 0x00, 0x00, 0x00];
        let out = c166_disasm(&d, vec![(0, 8)]);
        assert_eq!(out[0].2, "EXTP");
        assert_eq!(out[1].2, "EXTS");
    }
}
