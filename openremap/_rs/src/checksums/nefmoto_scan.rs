//! NefMoto ME7 helpers — 1:1 ports of the Python byte loops in
//! `core/services/nefmoto.py`:
//!
//! - `locate_pattern`   — masked byte-pattern scan (step 2, early exit)
//! - `rolling_checksum` — NefMoto rolling hash over inclusive byte ranges
//!
//! Parity verified against the Python implementations on 236 real ME7.x
//! corpus files + synthetic edge cases (2026-08-15, see
//! docs/rust-migration-audit.md).

use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;

#[pyfunction]
pub fn locate_pattern(
    data: &[u8],
    pat: &[u8],
    mask: &[u8],
    offset: i64,
    max_offset: i64,
    step: usize,
) -> i64 {
    let n = data.len();
    let limit = if max_offset < 0 {
        n
    } else {
        (max_offset as usize).min(n)
    };

    // Python's range(offset, limit, step) raises ValueError on step=0;
    // no caller uses it — return -1 defensively.
    if step == 0 {
        return -1;
    }

    let mut x = offset;
    while x >= 0 && (x as usize) < limit {
        let xu = x as usize;
        let mut ok = true;
        for y in 0..pat.len() {
            let pos = xu + y;
            if pos >= n {
                ok = false;
                break;
            }
            if ((data[pos] ^ pat[y]) & mask[y]) != 0 {
                ok = false;
                break;
            }
        }
        if ok {
            return x;
        }
        x += step as i64;
    }
    -1
}

#[pyfunction]
pub fn rolling_checksum(
    data: &[u8],
    seed_table_offset: usize,
    ranges: Vec<(i64, i64)>,
    init: u32,
) -> PyResult<u32> {
    let mut checksum = init;

    for (start, end) in ranges {
        // Python semantics: negative indices would wrap; no caller does
        // this — clamp to 0.  Empty ranges (end < start) produce no
        // iterations in Python either.
        if end < start {
            continue;
        }
        let s = start.max(0) as usize;
        for i in s..=(end as usize) {
            if i >= data.len() {
                // Python raises IndexError here — keep the error, not a
                // silent skip.
                return Err(PyIndexError::new_err("index out of range"));
            }
            let current_byte = data[i] as u32;
            let idx = seed_table_offset + (((current_byte ^ (checksum & 0xFF)) << 2) as usize);
            if idx + 4 > data.len() {
                continue;
            }
            let seed = u32::from_le_bytes([data[idx], data[idx + 1], data[idx + 2], data[idx + 3]]);
            checksum = (checksum >> 8) ^ seed;
        }
    }

    Ok(checksum)
}
