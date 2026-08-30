//! MCS-96 (8096 / 80196) reference extraction — the xref signal's decoder.
//!
//! A size-table + reference-form decoder for the Intel MCS-96 family
//! (EDC1 uses the 8096; census §6-C).  No capstone support, so this is the
//! Rust decoder (the C166/8051 ones' sibling).
//!
//! The MCS-96 reaches data through a register file (R0–R255; R0 is hardwired
//! 0) plus an offset, so the 16-bit data references are the ``ld reg,#imm16``
//! immediates (the base a later indexed access uses) and the ``ljmp``/``lcall``
//! absolute targets.  Address space is flat (address == file offset for the
//! ≤64 KB 8096 images).
//!
//! Ground truth: the public Intel MCS-96 ISA.  The size table is derived from
//! MAME's `src/devices/cpu/mcs96/mcs96ops.lst` (BSD-3-Clause — oracle only,
//! no code copied) and cross-checked against Ghidra's MCS96 disassembler.
//!
//! Size determination: 1 opcode byte + the addressing-mode byte count, with
//! two wrinkles — the ``0xFE`` prefix (a second opcode byte, the 80196
//! extensions) and the *indexed* addressing modes, where the base register's
//! bit 0 selects an 8-bit (short) or 16-bit (long) offset.

use pyo3::prelude::*;

/// Per-opcode table: the long (16-bit-offset) addressing-mode byte count,
/// with bit 7 set when the mode is *indexed* (variable short/long).
/// ``0`` also covers the reserved opcodes (rendered as 1-byte).
const SIZES: [u8; 256] = [
    0, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 0, 2, 2, 2, 2,
    0, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,
    3, 4, 131, 133, 3, 4, 131, 133, 3, 4, 131, 133, 3, 4, 131, 133,
    3, 3, 3, 133, 3, 3, 3, 133, 3, 3, 3, 133, 3, 3, 3, 133,
    2, 3, 2, 132, 2, 3, 2, 132, 2, 3, 2, 132, 2, 3, 2, 132,
    2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132,
    2, 3, 2, 132, 2, 3, 2, 132, 2, 3, 2, 132, 2, 3, 2, 132,
    2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132,
    2, 3, 2, 132, 2, 3, 2, 132, 2, 3, 2, 132, 2, 2, 2, 132,
    2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132, 2, 2, 2, 132,
    2, 2, 2, 132, 2, 2, 2, 132, 1, 2, 1, 131, 1, 2, 1, 131,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    2, 2, 0, 1, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 2,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

/// 0xFE-prefixed (80196 extension) opcodes, keyed by the second byte.
const PREFIX: [u8; 256] = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 4, 131, 133,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 3, 3, 133,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3, 2, 132,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 132,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3, 2, 132,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 132,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

/// Instruction length in bytes for the instruction at `data[off]`.
fn insn_size(data: &[u8], off: usize) -> usize {
    let b0 = data[off];
    let (pre, info) = if b0 == 0xFE {
        (2, PREFIX[data[off + 1] as usize])
    } else {
        (1, SIZES[b0 as usize])
    };
    let long_amode = (info & 0x7F) as usize;
    let indexed = info & 0x80 != 0;
    let amode = if indexed {
        // base register byte = data[off + pre]; bit 0 selects short/long.
        if data[off + pre] & 0x01 != 0 {
            long_amode
        } else {
            long_amode - 1
        }
    } else {
        long_amode
    };
    pre + amode
}

/// The reference-bearing forms: ``ld reg,#imm16`` (0xA1), ``ljmp`` (0xE7),
/// ``lcall`` (0xEF) — each carries a 16-bit little-endian address at bytes
/// +1..+2.
fn is_reference(b0: u8) -> bool {
    matches!(b0, 0xA1 | 0xE7 | 0xEF)
}

/// Collect the 16-bit data/code references of the MCS-96 code regions.
#[pyfunction]
pub fn mcs96_references(data: &[u8], regions: Vec<(i64, i64)>) -> (Vec<(u32, u32)>, u32) {
    let mut out: Vec<(u32, u32)> = Vec::new();
    let mut insn_count: u32 = 0;
    for_each_insn(data, &regions, |off, _size| {
        insn_count += 1;
        if is_reference(data[off]) {
            let addr = data[off + 1] as u32 | ((data[off + 2] as u32) << 8);
            out.push((addr, off as u32));
        }
    });
    (out, insn_count)
}

/// Decode the instruction stream of the MCS-96 code regions.
#[pyfunction]
pub fn mcs96_walk(data: &[u8], regions: Vec<(i64, i64)>) -> Vec<(u32, u8)> {
    let mut out: Vec<(u32, u8)> = Vec::new();
    for_each_insn(data, &regions, |off, size| {
        out.push((off as u32, size as u8));
    });
    out
}

/// Run *f* for every decoded instruction in the code regions.
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
            let size = insn_size(data, off);
            if off + size <= end {
                f(off, size);
                off += size;
            } else {
                break;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sz(bytes: &[u8]) -> usize {
        insn_size(bytes, 0)
    }

    #[test]
    fn sizes_of_known_forms() {
        assert_eq!(sz(&[0x02, 0x46]), 2); // NOT (register-direct)
        assert_eq!(sz(&[0xFD]), 1); // NOP
        assert_eq!(sz(&[0xFF]), 1); // RST
        assert_eq!(sz(&[0x00]), 1); // SKIP (1-byte)
        assert_eq!(sz(&[0x30, 0x00, 0x00]), 3); // JBC bit,rel
        assert_eq!(sz(&[0xD5, 0x00]), 2); // JNV rel8
        assert_eq!(sz(&[0xE7, 0x00, 0x00]), 3); // LJMP addr16
        assert_eq!(sz(&[0xA1, 0x34, 0x12, 0x1C]), 4); // LD reg,#imm16
    }

    #[test]
    fn indexed_short_vs_long() {
        // 0x63 = AND [base+off],reg — base bit0=0 (short, 8-bit offset) vs 1 (long).
        assert_eq!(sz(&[0x63, 0x00, 0x00, 0x00]), 4); // short (base+8bit+reg)
        assert_eq!(sz(&[0x63, 0x01, 0x00, 0x00, 0x00]), 5); // long (base+16bit+reg)
    }

    #[test]
    fn references_collect_ld_and_jumps() {
        // LD reg,#0x1234 (A1 34 12 1C), LJMP 0x5678 (E7 78 56)
        let d = [0xA1, 0x34, 0x12, 0x1C, 0xE7, 0x78, 0x56];
        let (refs, insns) = mcs96_references(&d, vec![(0, 7)]);
        assert_eq!(refs, vec![(0x1234, 0), (0x5678, 4)]);
        assert_eq!(insns, 2);
    }
}
