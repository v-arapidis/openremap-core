//! Denso Subaru checksum table detection — 1:1 port of
//! `core/services/checksums/denso.py` (the Python implementation's scan
//! loop measured ~1 s per MB; parity verified before the Python loop was
//! erased — see docs/rust-migration-audit.md).
//!
//! Ground truth: RomRaider `RomChecksum.java` (GPL) + EcuFlash defs
//! (td-d/SubaruDefs).  Descriptor entries are 12 bytes:
//! `[start BE32][end BE32][diff BE32]`; the sum target is
//! `0x5AA5A55A` and the `end` address is INCLUSIVE of the last byte
//! (sum over `[start, end+1)`, 4-aligned).  `start == 0 && end == 0`
//! marks a disabled entry.

use pyo3::prelude::*;

const CHECK_TOTAL: u32 = 0x5AA5_A55A;
const ENTRY_SIZE: usize = 12;
const MIN_ENTRIES: usize = 3;
const MAX_ENTRIES: usize = 32;

/// BE32 read at *i*.
#[inline]
fn u32be(data: &[u8], i: usize) -> u32 {
    u32::from_be_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]])
}

/// Prefix sums of BE32 words: prefix[k] = sum of words [0, k*4) mod 2^32.
fn prefix_sums(data: &[u8]) -> Vec<u32> {
    let wcount = data.len() / 4;
    let mut prefix = vec![0u32; wcount + 1];
    for k in 0..wcount {
        prefix[k + 1] = prefix[k].wrapping_add(u32be(data, k * 4));
    }
    prefix
}

/// Sum of BE32 words over [start, end_excl); both must be 4-aligned.
#[inline]
fn word_sum(prefix: &[u32], start: usize, end_excl: usize) -> u32 {
    prefix[end_excl / 4].wrapping_sub(prefix[start / 4])
}

/// Cheap structural filter — no prefix sums, no verification.
fn structurally_plausible(data: &[u8], i: usize) -> bool {
    if i + ENTRY_SIZE > data.len() {
        return false;
    }
    let start = u32be(data, i);
    let end = u32be(data, i + 4);
    if start == 0 && end == 0 {
        return true;
    }
    start % 4 == 0
        && (end & 3) == 3
        && 0 < start
        && (start as usize) < (end as usize + 1)
        && end as usize + 1 <= data.len()
        && (end + 1) % 4 == 0
}

/// Classify the entry at *i*: 0 = not plausible, 1 = ok, 2 = stale, 3 = disabled.
fn classify(data: &[u8], prefix: &[u32], i: usize) -> u8 {
    if i + ENTRY_SIZE > data.len() {
        return 0;
    }
    let start = u32be(data, i);
    let end = u32be(data, i + 4);
    let diff = u32be(data, i + 8);
    if start == 0 && end == 0 {
        return 3;
    }
    let e_excl = end as usize + 1;
    if start % 4 != 0
        || (end & 3) != 3
        || start == 0
        || e_excl <= start as usize
        || e_excl > data.len()
        || e_excl % 4 != 0
    {
        return 0;
    }
    let total = word_sum(prefix, start as usize, e_excl);
    if total.wrapping_add(diff) == CHECK_TOTAL {
        1
    } else {
        2
    }
}

#[pyfunction]
#[pyo3(signature = (data))]
pub fn detect_denso(data: &[u8]) -> PyResult<Option<(usize, Vec<(usize, i64, i64, i64, i64, String)>)>> {
    if data.len() < 0x4000 {
        return Ok(None);
    }

    let prefix = prefix_sums(data);
    let n = data.len();
    let mut i = 0usize;

    while i + ENTRY_SIZE <= n {
        // Cheap pre-filter before unpacking: BE start %4==0, BE end %4==3.
        if (data[i + 3] & 3) != 0 || (data[i + 7] & 3) != 3 {
            i += 1;
            continue;
        }
        if classify(data, &prefix, i) != 1 {
            i += 1;
            continue;
        }

        // Walk backward (bounded) to include a stale head.
        let mut start_i = i;
        for _ in 0..MAX_ENTRIES {
            if start_i < ENTRY_SIZE {
                break;
            }
            if !structurally_plausible(data, start_i - ENTRY_SIZE) {
                break;
            }
            start_i -= ENTRY_SIZE;
        }

        // Read the run forward (ok / stale / disabled).
        let mut entries: Vec<(usize, i64, i64, i64, i64, String)> = Vec::new();
        let mut verified = 0usize;
        let mut k = 0usize;
        while k < MAX_ENTRIES {
            let off = start_i + k * ENTRY_SIZE;
            if off + ENTRY_SIZE > n {
                break;
            }
            let c = classify(data, &prefix, off);
            if c == 0 {
                break;
            }
            let start = u32be(data, off) as i64;
            let end = u32be(data, off + 4) as i64;
            let diff = u32be(data, off + 8) as i64;
            if c == 3 {
                entries.push((k, 0, 0, 0, 0, "disabled".to_string()));
            } else {
                let expected = CHECK_TOTAL
                    .wrapping_sub(word_sum(&prefix, start as usize, end as usize + 1))
                    as i64;
                let status = if c == 1 { "ok" } else { "stale" };
                if c == 1 {
                    verified += 1;
                }
                entries.push((k, start, end, diff, expected, status.to_string()));
            }
            k += 1;
        }

        if verified >= MIN_ENTRIES {
            return Ok(Some((start_i, entries)));
        }

        // Skip the whole scanned run so we don't re-seed inside it.
        let run_len = entries.len() * ENTRY_SIZE;
        i = i.max(start_i + run_len);
    }

    Ok(None)
}
