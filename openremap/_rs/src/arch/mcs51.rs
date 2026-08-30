//! MCS-51 (8051) reference extraction — the xref signal's decoder.
//!
//! A size-table + reference-form decoder for the Intel MCS-51 (8051)
//! family — M2.x / MP9 / M4.x / Mono / SIMOS / Simtec56 (census §6-C).
//! The 8051 has no capstone support, so this is the Rust decoder (the C166
//! one's sibling) that the xref pass needs for those families.
//!
//! Unlike C166 — whose data references are embedded in a 16-bit memory
//! operand (`MOV Rwn, mem`) — the 8051 reaches tables through the DPTR
//! register.  The ONLY instruction that loads a 16-bit immediate is
//! ``MOV DPTR, #data16`` (0x90, big-endian high-then-low); the following
//! ``MOVC A,@A+DPTR`` / ``MOVX A,@DPTR`` / ``MOVX @DPTR,A`` read from that
//! base.  So the xref signal collects the 16-bit immediate of every
//! ``MOV DPTR, #data16`` — directly analogous to the C166 embedded operand.
//!
//! Address space is flat (address == file offset for the ≤64 KB 8051
//! images), so no load-base translation is needed (unlike C166's DPP
//! window or TriCore's base candidates).
//!
//! Ground truth: the public Intel MCS-51 instruction set (THIRD_PARTY.md).
//! No code is copied from external decoders — `mmastrac/i8051` and
//! `8051Enthusiast/at51` (both MIT) are reference oracles only (census §6-C).

use pyo3::prelude::*;

/// Instruction length in bytes (1, 2 or 3) for the first byte of an MCS-51
/// instruction — the public Intel opcode map.
fn insn_size(b0: u8) -> usize {
    match b0 {
        // ── 3-byte forms ─────────────────────────────────────────────────
        0x02 | 0x12 |                    // LJMP addr16 / LCALL addr16
        0x10 | 0x20 | 0x30 |            // JBC / JB / JNB bit, rel
        0x43 | 0x53 | 0x63 |            // ORL / ANL / XRL direct, #data
        0x75 | 0x85 |                    // MOV direct, #data / direct, direct
        0x90 |                           // MOV DPTR, #data16
        0xB4 | 0xB5 | 0xB6 | 0xB7 |     // CJNE A,# / A,direct / @R0,# / @R1,#
        0xD5 => 3,                       // DJNZ direct, rel
        0xB8..=0xBF => 3,                // CJNE Rn, #data, rel
        // ── 2-byte forms ─────────────────────────────────────────────────
        0x01 | 0x11 | 0x21 | 0x31 | 0x41 | 0x51 | 0x61 | 0x71 | 0x81
        | 0x91 | 0xA1 | 0xB1 | 0xC1 | 0xD1 | 0xE1 | 0xF1 => 2, // AJMP/ACALL
        0x05 | 0x15 => 2,                // INC / DEC direct
        0x24 | 0x34 | 0x44 | 0x54 | 0x64 | 0x74 | 0x94 => 2,   // A, #data
        0x25 | 0x35 | 0x45 | 0x55 | 0x65 | 0x95 | 0xE5 => 2,   // A, direct
        0x42 | 0x52 | 0x62 => 2,         // ORL / ANL / XRL direct, A
        0x40 | 0x50 | 0x60 | 0x70 | 0x80 => 2,                 // JC/JNC/JZ/JNZ/SJMP rel
        0x72 | 0x82 | 0x92 | 0xA0 | 0xA2 | 0xB0 | 0xB2 => 2,   // bit ops (ORL/ANL/MOV C/CPL)
        0xC0 | 0xC2 | 0xD0 | 0xD2 => 2,  // PUSH / CLR bit / POP / SETB bit
        0x76 | 0x77 => 2,                // MOV @Ri, #data
        0x78..=0x7F => 2,                // MOV Rn, #data
        0x86 | 0x87 => 2,                // MOV direct, @Ri
        0x88..=0x8F => 2,                // MOV direct, Rn
        0xA6 | 0xA7 => 2,                // MOV @Ri, direct
        0xA8..=0xAF => 2,                // MOV Rn, direct
        0xC5 => 2,                       // XCH A, direct
        0xD8..=0xDF => 2,                // DJNZ Rn, rel
        0xF5 => 2,                       // MOV direct, A
        // ── 1-byte (implied / register / accumulator) ───────────────────
        _ => 1,
    }
}

/// True when the opcode carries a 16-bit data reference — ``MOV DPTR,
/// #data16`` (0x90), the 8051's only 16-bit-immediate load.
fn is_reference(b0: u8) -> bool {
    b0 == 0x90
}

/// Collect the DPTR-base data references of the MCS-51 code regions.
///
/// Returns ``((addr16, insn_file_offset) pairs, instruction_count)``: the
/// 16-bit ``MOV DPTR, #data16`` immediates (big-endian high-then-low) and
/// the file offset of the referencing instruction, plus the number of
/// decoded instructions (walk steps).  The caller hit-tests the addresses
/// against table spans (identity-mapped — address == file offset).
#[pyfunction]
pub fn mcs51_references(data: &[u8], regions: Vec<(i64, i64)>) -> (Vec<(u32, u32)>, u32) {
    let mut out: Vec<(u32, u32)> = Vec::new();
    let mut insn_count: u32 = 0;
    for_each_insn(data, &regions, |off, _size| {
        insn_count += 1;
        if is_reference(data[off]) {
            let addr = ((data[off + 1] as u32) << 8) | data[off + 2] as u32;
            out.push((addr, off as u32));
        }
    });
    (out, insn_count)
}

/// Decode the instruction stream of the MCS-51 code regions.
///
/// Returns ``(insn_file_offset, length)`` for every decoded instruction —
/// the raw walk, mirroring `c166_walk`.
#[pyfunction]
pub fn mcs51_walk(data: &[u8], regions: Vec<(i64, i64)>) -> Vec<(u32, u8)> {
    let mut out: Vec<(u32, u8)> = Vec::new();
    for_each_insn(data, &regions, |off, size| {
        out.push((off as u32, size as u8));
    });
    out
}

/// Run *f* for every decoded instruction in the code regions.
///
/// Shared walk used by `mcs51_references` and `mcs51_walk`: same size
/// table, same region bounds, so the two exports can never drift apart.
/// Only instructions that **fully fit inside the region** are emitted.
/// (MCS-51 minimum size is 1 byte, so the loop runs to the region end.)
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
        while off < end {
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

    #[test]
    fn sizes_of_known_forms() {
        assert_eq!(insn_size(0x00), 1); // NOP
        assert_eq!(insn_size(0x04), 1); // INC A
        assert_eq!(insn_size(0xE8), 1); // MOV A, R0
        assert_eq!(insn_size(0xF8), 1); // MOV R0, A
        assert_eq!(insn_size(0xE0), 1); // MOVX A, @DPTR
        assert_eq!(insn_size(0x74), 2); // MOV A, #data
        assert_eq!(insn_size(0xE5), 2); // MOV A, direct
        assert_eq!(insn_size(0xF5), 2); // MOV direct, A
        assert_eq!(insn_size(0x78), 2); // MOV R0, #data
        assert_eq!(insn_size(0x01), 2); // AJMP
        assert_eq!(insn_size(0x02), 3); // LJMP
        assert_eq!(insn_size(0x12), 3); // LCALL
        assert_eq!(insn_size(0x75), 3); // MOV direct, #data
        assert_eq!(insn_size(0x85), 3); // MOV direct, direct
        assert_eq!(insn_size(0x90), 3); // MOV DPTR, #data16
        assert_eq!(insn_size(0xB4), 3); // CJNE A, #data, rel
        assert_eq!(insn_size(0xBF), 3); // CJNE R7, #data, rel
        assert_eq!(insn_size(0xD5), 3); // DJNZ direct, rel
        assert_eq!(insn_size(0x10), 3); // JBC bit, rel
    }

    #[test]
    fn walk_aligns_on_ljmp_vector_table() {
        // A real 8051 reset/interrupt table: LJMP at 0x00, 0x03, 0x0B, 0x13.
        let d = [
            0x02, 0x0F, 0x00, // LJMP 0x0F00
            0x02, 0x0E, 0x00, // LJMP 0x0E00
            0x00,             // (padding) NOP
            0x02, 0x0D, 0x00, // LJMP 0x0D00
            0x02, 0x0C, 0x00, // LJMP 0x0C00
        ];
        let walk = mcs51_walk(&d, vec![(0, 13)]);
        assert_eq!(
            walk,
            vec![(0, 3), (3, 3), (6, 1), (7, 3), (10, 3)]
        );
    }

    #[test]
    fn references_collect_mov_dptr_immediate() {
        // MOV DPTR, #0x1234 = 90 12 34 — the only 16-bit data reference.
        let d = [0x90, 0x12, 0x34, 0x00, 0x90, 0xAB, 0xCD];
        let (refs, insn_count) = mcs51_references(&d, vec![(0, 7)]);
        assert_eq!(refs, vec![(0x1234, 0), (0xABCD, 4)]);
        assert_eq!(insn_count, 3);
    }

    #[test]
    fn references_ignore_other_forms() {
        // MOV A, #0x12 (74 12) is NOT a 16-bit reference; only MOV DPTR is.
        let d = [0x74, 0x12, 0x90, 0x00, 0x80];
        let (refs, _) = mcs51_references(&d, vec![(0, 5)]);
        assert_eq!(refs, vec![(0x0080, 2)]);
    }

    /// Canonical MCS-51 instruction lengths from the `8051Enthusiast/at51`
    /// decoder (MIT) — the reference oracle the size table is cross-checked
    /// against (census §6-C).  Verified 256/256 on 2026-08-29.
    const AT51_LENGTHS: [u8; 256] = [
        1, 2, 3, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        3, 2, 3, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        3, 2, 1, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        3, 2, 1, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 1, 2, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
        2, 2, 2, 1, 1, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
        3, 2, 2, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
        2, 2, 2, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
        2, 2, 2, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        2, 2, 2, 1, 1, 3, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2,
        1, 2, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
        1, 2, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    ];

    #[test]
    fn sizes_match_at51_oracle() {
        for (op, &expected) in AT51_LENGTHS.iter().enumerate() {
            assert_eq!(insn_size(op as u8), expected as usize, "opcode 0x{op:02X}");
        }
    }
}
