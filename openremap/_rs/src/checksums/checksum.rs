//! Checksum sweep engine — brute-force verification of candidate checksum
//! schemes over a binary, at native speed.
//!
//! The automotive checksum space is large but structured: a handful of
//! algorithm families (byte/word sums, XOR, CRC-8/16/32) parameterized by
//! init value, final XOR, region boundaries, and where the stored value
//! lives.  `checksum_sweep` tests every supplied config and reports which
//! ones verify — a random config accidentally matching a 16-bit stored
//! value has probability ~1/65536, so FAMILY CONSENSUS across many files
//! (same config verifies on 90%+ of a family's stock files) is proof of
//! the real scheme.

use pyo3::prelude::*;

// Algorithm ids.
pub const SUM8: u8 = 0; // sum of bytes mod 256
pub const SUM16: u8 = 1; // sum of bytes mod 65536
pub const SUM16_LE: u8 = 2; // sum of u16 words (little-endian) mod 65536
pub const SUM16_BE: u8 = 3; // sum of u16 words (big-endian) mod 65536
pub const XOR8: u8 = 4; // XOR of bytes
pub const XOR16_LE: u8 = 5; // XOR of u16 words (little-endian)
pub const XOR16_BE: u8 = 6; // XOR of u16 words (big-endian)
pub const CRC8: u8 = 7; // CRC-8 (poly 0x07, reflected)
pub const CRC16_CCITT: u8 = 8; // poly 0x1021 (non-reflected, init param)
pub const CRC16_ARC: u8 = 9; // poly 0x8005 reflected (init param)
pub const CRC32_IEEE: u8 = 10; // poly 0xEDB88320 reflected (init param)
pub const SUM16LE_ACC32: u8 = 11; // LE u16 words accumulated into u32 (ME7 main checksum)
pub const SUM16BE_ACC32: u8 = 12; // BE u16 words accumulated into u32

const fn make_crc8_table() -> [u8; 256] {
    let mut t = [0u8; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u8;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0x8C } else { c >> 1 };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
}
const fn make_crc16_ccitt_table() -> [u16; 256] {
    let mut t = [0u16; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = (i as u16) << 8;
        let mut k = 0;
        while k < 8 {
            c = if c & 0x8000 != 0 { (c << 1) ^ 0x1021 } else { c << 1 };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
}
const fn make_crc16_arc_table() -> [u16; 256] {
    let mut t = [0u16; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u16;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0xA001 } else { c >> 1 };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
}
const fn make_crc32_table() -> [u32; 256] {
    let mut t = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0xEDB8_8320 } else { c >> 1 };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
}

static CRC8_TABLE: [u8; 256] = make_crc8_table();
static CRC16_CCITT_TABLE: [u16; 256] = make_crc16_ccitt_table();
static CRC16_ARC_TABLE: [u16; 256] = make_crc16_arc_table();
static CRC32_TABLE: [u32; 256] = make_crc32_table();

/// One compute job, packed as a tuple:
/// (algo, init, region_start, region_end)  — ``region_end == -1`` means
/// "to end of data".  Returns the raw computed value for each job; the
/// (final XOR, stored value, endianness, complement) comparisons are
/// cheap and belong in Python where the config space explodes for free.
#[pyfunction(signature = (data, jobs))]
pub fn checksum_compute(data: Vec<u8>, jobs: Vec<(u8, u64, i64, i64)>) -> Vec<u64> {
    let mut out = Vec::with_capacity(jobs.len());
    for (algo, init, rs, re) in jobs {
        let start = if rs < 0 { 0 } else { rs as usize };
        let end = if re < 0 { data.len() } else { (re as usize).min(data.len()) };
        if start >= end || start > data.len() {
            out.push(u64::MAX);
            continue;
        }
        let region = &data[start..end];
        let computed: u64 = match algo {
            SUM8 => { let mut s: u64 = init & 0xFF; for &b in region { s += b as u64; } s & 0xFF }
            SUM16 => { let mut s: u64 = init & 0xFFFF; for &b in region { s += b as u64; } s & 0xFFFF }
            SUM16_LE | SUM16_BE => {
                let le = algo == SUM16_LE;
                let mut s: u64 = init & 0xFFFF;
                let n = region.len() & !1usize;
                for i in (0..n).step_by(2) {
                    let w = if le { region[i] as u64 | ((region[i + 1] as u64) << 8) }
                             else { ((region[i] as u64) << 8) | region[i + 1] as u64 };
                    s += w;
                }
                if n < region.len() { s += region[n] as u64; }
                s & 0xFFFF
            }
            XOR8 => { let mut s: u64 = init & 0xFF; for &b in region { s ^= b as u64; } s }
            XOR16_LE | XOR16_BE => {
                let le = algo == XOR16_LE;
                let mut s: u64 = init & 0xFFFF;
                let n = region.len() & !1usize;
                for i in (0..n).step_by(2) {
                    let w = if le { region[i] as u64 | ((region[i + 1] as u64) << 8) }
                             else { ((region[i] as u64) << 8) | region[i + 1] as u64 };
                    s ^= w;
                }
                if n < region.len() { s ^= region[n] as u64; }
                s
            }
            CRC8 => {
                let mut crc: u8 = (init & 0xFF) as u8;
                for &b in region {
                    crc = CRC8_TABLE[(crc ^ b) as usize];
                }
                crc as u64
            }
            CRC16_CCITT => {
                let mut crc: u16 = (init & 0xFFFF) as u16;
                for &b in region {
                    crc = (crc << 8) ^ CRC16_CCITT_TABLE[((crc >> 8) ^ b as u16) as usize];
                }
                crc as u64
            }
            CRC16_ARC => {
                let mut crc: u16 = (init & 0xFFFF) as u16;
                for &b in region {
                    crc = CRC16_ARC_TABLE[((crc ^ b as u16) & 0xFF) as usize] ^ (crc >> 8);
                }
                crc as u64
            }
            SUM16LE_ACC32 | SUM16BE_ACC32 => {
                let le = algo == SUM16LE_ACC32;
                let mut acc: u64 = init & 0xFFFF_FFFF;
                let n = region.len() & !1usize;
                for i in (0..n).step_by(2) {
                    let w = if le {
                        region[i] as u64 | ((region[i + 1] as u64) << 8)
                    } else {
                        ((region[i] as u64) << 8) | region[i + 1] as u64
                    };
                    acc = acc.wrapping_add(w) & 0xFFFF_FFFF;
                }
                if n < region.len() {
                    acc = (acc + region[n] as u64) & 0xFFFF_FFFF;
                }
                acc
            }
            CRC32_IEEE => {
                let mut crc: u32 = (init & 0xFFFF_FFFF) as u32;
                for &b in region {
                    crc = CRC32_TABLE[((crc ^ b as u32) & 0xFF) as usize] ^ (crc >> 8);
                }
                crc as u64
            }
            _ => { out.push(u64::MAX); continue; }
        };
        out.push(computed);
    }
    out
}

/// Scan for Bosch ME7 multipoint checksum blocks.
///
/// Each block is a 16-byte descriptor at a 2-aligned offset: (start, end,
/// checksum, ~checksum) as four LE u32.  Returns (offset, start, end,
/// checksum, valid) where valid means the u16-LE sum (u32 accumulator)
/// over [start-base, end-base) equals the stored checksum.
#[pyfunction(signature = (data, base = 0x800000))]
pub fn me7_multipoint_scan(data: Vec<u8>, base: u32) -> Vec<(usize, u32, u32, u32, bool)> {
    let mut out = Vec::new();
    let n = data.len();
    if n < 16 {
        return out;
    }
    let mut i = 0;
    while i + 16 <= n {
        let start = u32::from_le_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]);
        let end = u32::from_le_bytes([data[i + 4], data[i + 5], data[i + 6], data[i + 7]]);
        let cks = u32::from_le_bytes([data[i + 8], data[i + 9], data[i + 10], data[i + 11]]);
        let inv = u32::from_le_bytes([data[i + 12], data[i + 13], data[i + 14], data[i + 15]]);
        if cks == !inv {
            if start >= base && end >= base && end > start {
                let sb = (start - base) as usize;
                let eb = (end - base) as usize;
                // plausibility: range must live inside the dump and be a
                // real block (rejects shifted-window artifacts inside the
                // descriptor table)
                if eb <= n && eb - sb <= 0x100000 {
                    let mut acc: u64 = 0;
                    let m = eb & !1usize;
                    let mut k = sb;
                    while k < m {
                        let w = data[k] as u64 | ((data[k + 1] as u64) << 8);
                        acc = acc.wrapping_add(w) & 0xFFFF_FFFF;
                        k += 2;
                    }
                    if m < eb {
                        acc = (acc + data[m] as u64) & 0xFFFF_FFFF;
                    }
                    let valid = (acc as u32) == cks;
                    out.push((i, start, end, cks, valid));
                }
            }
        }
        i += 2;
    }
    out
}

#[inline]
fn read_store(data: &[u8], off: usize, size: usize, le: bool) -> u64 {
    let mut v: u64 = 0;
    if le {
        for i in 0..size {
            v |= (data[off + i] as u64) << (8 * i);
        }
    } else {
        for i in 0..size {
            v = (v << 8) | data[off + i] as u64;
        }
    }
    v
}
