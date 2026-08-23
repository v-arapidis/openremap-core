//! CRC-16/ARC (poly 0x8005 reflected) — 1:1 port of `ms43.crc16_arc`.
//!
//! Table-driven CRC over inclusive byte blocks with the same block
//! guards as the Python implementation (negative start, end past the
//! data, or inverted ranges are skipped; ``init`` is masked to 16 bits).
//!
//! Parity verified against Python on the MS43 corpus + the standard
//! CRC-16/ARC check value ("123456789" → 0xBB3D) and synthetic edges
//! (2026-08-15, see docs/rust-migration-audit.md).

use pyo3::prelude::*;

#[pyfunction]
pub fn crc16_arc(data: &[u8], blocks: Vec<(i64, i64)>, init: u32) -> u16 {
    let mut table = [0u16; 256];
    for i in 0..256u16 {
        let mut c = i;
        for _ in 0..8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0xA001 } else { c >> 1 };
        }
        table[i as usize] = c;
    }

    let mut crc = (init & 0xFFFF) as u16;
    for (s, e) in blocks {
        if s < 0 || e >= data.len() as i64 || s > e {
            continue;
        }
        for i in s..=e {
            crc = table[((crc ^ data[i as usize] as u16) & 0xFF) as usize] ^ (crc >> 8);
        }
    }
    crc
}
